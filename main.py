import os
import re
import urllib
import datetime

from pprint import pprint as pp

import pytz
import nltk
import requests
import firebase_admin

from bs4 import BeautifulSoup
from firebase_admin import db, credentials
from apscheduler.schedulers.blocking import BlockingScheduler

import DC
import ADO



SCHED = BlockingScheduler()
MEXICO_CITY_TIMEZONE = pytz.timezone('America/Mexico_City')

# APIs
MS_GRAPH_URL = 'https://graph.microsoft.com/v1.0'
TELEGRAM_URL = 'https://api.telegram.org'

# Months!
MONTHS = {
    'ENE': '01',
    'FEB': '02',
    'MAR': '03',
    'ABR': '04',
    'MAY': '05',
    'JUN': '06',
    'JUL': '07',
    'AGO': '08',
    'SEP': '09',
    'OCT': '10',
    'NOV': '11',
    'DIC': '12'
}

def initialize_firebase():
    '''
    Iniitializes fiirebase with .env values
    '''
    cert = credentials.Certificate({
        'type': 'service_account',
        'token_uri': 'https://oauth2.googleapis.com/token',
        'private_key': os.getenv('PRIVATE_KEY').replace('\\n', '\n'),
        'client_email': os.getenv('CLIENT_EMAIL')
    })
    firebase_admin.initialize_app(cert, {'databaseURL': os.getenv('DATABASE_URL')})

def get_token():
    '''
    Gets a new valid token and stores a new refresh token
    '''
    print('Getting new token...')
    # get refresh token
    refresh_token = db.reference('refresh_tokens/hotmail').get()
    # list token scopes
    scopes = [
        'offline_access',
        'user.readwrite',
        'mail.read',
        'mail.send',
        'mail.readwrite'
    ]
    # build POST parameters
    params = {
        'client_id': os.getenv('CLIENT_ID'),
        'scope': ' '.join(scopes),
        'refresh_token': refresh_token,
        'redirect_uri': 'http://localhost:5000/',
        'client_secret': os.getenv('CLIENT_SECRET'),
        'grant_type': 'refresh_token'
    }
    # Make POST request to MS
    url = 'https://login.microsoftonline.com/consumers/oauth2/v2.0/token'
    response = requests.post(url, data=params).json()

    # Get new refresh token from response
    refresh_token = response['refresh_token']

    # Store new refresh token in firebase
    db_ref = db.reference('/refresh_tokens')
    db_ref.update({'hotmail': refresh_token})

    # return access_token
    return response['access_token']

def gather_emails(token, folder_id):
    '''
    Gathers all outlook emails within a folder
    '''
    headers = {
        'Authorization': token
    }
    response = requests.get(f'{MS_GRAPH_URL}/me/mailFolders/{folder_id}/messages', headers=headers)
    emails = response.json()['value']
    return emails


def delete_emails_in_folder(emails, token, folder_id):
    '''
    Deletes all given emails from the given folder
    '''
    headers = {
        'Authorization': token
    }
    for email in emails:
        subject = email['subject']
        email_id = email['id']

        print(f'Deleting {subject}... ', end='')
        url = f'{MS_GRAPH_URL}/me/mailFolders/{folder_id}/messages/{email_id}'
        response = requests.delete(url, headers=headers)
        print(response.status_code)



@SCHED.scheduled_job('interval', minutes=1)
def debit_and_credit_automation():
    '''
    Checks every minute for an email in D&C folder,
    extracts transaction information, builds an url schema for D&C,
    and sends it through telegram
    '''
    print("Debit & Credit automation...")
    token = get_token()
    emails = gather_emails(token, os.getenv('DEBIT_AND_CREDIT_FOLDER_ID'))

    transactions = []
    for email in emails:
        transaction = DC.process_email(email)
        if type(transaction) == dict:
            transactions.append(transaction)
        elif type(transaction) == list:
            transactions.extend(transaction)

    for transaction in transactions:
        transaction['account'] = 'BBVA Crédito'
        shortcuts_url = 'dcapp://x-callback-url/expense?'
        params = urllib.parse.urlencode(transaction, quote_via=urllib.parse.quote)

        url = f'{TELEGRAM_URL}/bot{os.getenv("TELEGRAM_BOT_TOKEN")}/sendMessage'
        data = {
            'chat_id': os.getenv('TELEGRAM_CHAT_ID'),
            'text': shortcuts_url+params
        }
        requests.post(url, data=data)

    delete_emails_in_folder(emails, token, os.getenv('DEBIT_AND_CREDIT_FOLDER_ID'))
    print("Debit & Credit Done.\n")


@SCHED.scheduled_job('cron', second=30, timezone=MEXICO_CITY_TIMEZONE)
def facturar_ado():
    '''
    Processes ADO emails, extracts info from their pdfs, and sends them to ADO
    '''
    print("Facturando ADO...")

    # Email handling
    token = get_token()
    emails = gather_emails(token, os.getenv('ADO_FOLDER_ID'))

    # Extract link from emails
    links = []
    for email in emails:
        soup = BeautifulSoup(email['body']['content'], 'html.parser')
        link = soup.find('a', string=re.compile('Boleto'))['href']
        links.append((link, email['id']))

    # Extract info from pdf
    tickets_info = []
    for link, email_id in links:
        tickets_info.extend(ADO.get_info_from_pdf_link(link, email_id))

    # Separate into main and other tickets,
    # and only grab tickets from last month
    main_tickets = []
    other_tickets = []
    for ticket in tickets_info:

        # Get datetime of ticket
        # Get abbreviation from ticket
        ticket_month_str = ticket['date'].split(' ')[1]
        # Get equivalent int from dictionary and replace it in date string
        ticket_date_str = ticket['date'].replace(ticket_month_str, MONTHS[ticket_month_str])
        # strptime and make it timezone aware
        ticket_date = datetime.datetime.strptime(ticket_date_str, '%d %m %y')
        ticket_date = MEXICO_CITY_TIMEZONE.localize(ticket_date)

        # Get last month datetime aware range
        # Get current date
        current_date = datetime.datetime.now(MEXICO_CITY_TIMEZONE)
        # From current date, go back to first instant of the current month
        first_instant_of_current_month = current_date.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        # From first instant of current month, substract a microsecond to get the last instant of previous month
        last_instant_of_previous_month = first_instant_of_current_month - datetime.timedelta(microseconds=1)
        # From the last instant of that previous month, go to the first instant of that previous month
        first_instant_of_previous_month = last_instant_of_previous_month.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # Check if ticket is in range
        if not first_instant_of_previous_month < ticket_date < last_instant_of_previous_month:
            continue

        # Get word distance to main passanger
        dist = nltk.edit_distance(ticket['name'], 'RAFAEL YOBAIN LUNA GOMEZ')
        if dist <= 5:
            main_tickets.append(ticket)
        else:
            other_tickets.append(ticket)

    pp(main_tickets)
    pp(other_tickets)

    # Facturar
    ADO.facturar_lote(main_tickets)
    ADO.facturar_lote(other_tickets)

    # Get info fro
    print("Facturando ADO Done\n")

initialize_firebase()
facturar_ado()
SCHED.start()
