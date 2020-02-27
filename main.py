'''
Main personal automation module
'''

import os
import re
import urllib
import datetime

from datetime import timedelta
from pprint import pprint as pp

import pytz
import nltk
import requests
import firebase_admin

from bs4 import BeautifulSoup
from firebase_admin import db, credentials
from apscheduler.schedulers.blocking import BlockingScheduler
from PyPDF2.utils import PdfReadError

import DC
import ADO



SCHED = BlockingScheduler()
MEXICO_CITY_TIMEZONE = pytz.timezone('America/Mexico_City')

# APIs
MS_GRAPH_URL = "https://graph.microsoft.com/v1.0"
TELEGRAM_URL = "https://api.telegram.org"

# URL Schemes
DC_EXPENSE_URL_SCHEME = "dcapp://x-callback-url/expense?"
DC_TRANSFER_URL_SCHEME = "dcapp://x-callback-url/transfer?"

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


def send_telegram_message(text):
    '''
    Sends a telegram message via PersonalAutomationBot to myself
    It parses text as html
    '''
    data = {
        "chat_id": os.getenv("TELEGRAM_CHAT_ID"),
        "text": text,
        "parse_mode": "html"
    }
    url = f'{TELEGRAM_URL}/bot{os.getenv("TELEGRAM_BOT_TOKEN")}/sendMessage'
    requests.post(url, data=data)


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

    # Build transactions from emails
    transactions = []
    emails_to_be_deleted = []
    for email in emails:

        # If no rule found, just skip to next email
        try:
            transaction = DC.process_email(email)
        except Exception:
            continue

        if type(transaction) == dict:
            transactions.append(transaction)
        elif type(transaction) == list:
            transactions.extend(transaction)

        # Add email to delete list
        emails_to_be_deleted.append(email)

    # Send all transactions as url-schemes via telegram
    for transaction in transactions:

        # Check if it's a transfer by looking for "source_account" in dict
        if "source_account" in transaction:
            shortcuts_url = DC_TRANSFER_URL_SCHEME
            text = "<b>Transferencia detectada</b>\n\n"
        # Else, it's an expene
        else:
            transaction["account"] = "BBVA Crédito"
            shortcuts_url = DC_EXPENSE_URL_SCHEME
            text = "<b>Gasto detectado</b>\n\n"

        # build url-scheme
        params = urllib.parse.urlencode(transaction, quote_via=urllib.parse.quote)

        # Build message text with format

        '''
        <b>[transation] detectadx</b>

        <b>Param1</b>: value1
        <b>Param2</b>: value2...

        <b>Date</b>: Date

        <b>URL scheme</b>: D&C URL scheme
        '''

        for key, item in transaction.items():
            text += f"<b>{key.title()}</b>: {item}\n"
        # Add current date
        date_string = datetime.datetime.now(MEXICO_CITY_TIMEZONE).strftime("%Y-%m-%d, %H:%M")
        text += f"\n<b>Date</b>: {date_string}\n\n"
        # Add URL scheme
        text += f"<b>D&C URL scheme</b>: {shortcuts_url+params}"
        # Send message
        send_telegram_message(text)

    # delete emails
    delete_emails_in_folder(emails_to_be_deleted, token, os.getenv('DEBIT_AND_CREDIT_FOLDER_ID'))
    print("Debit & Credit Done.\n")


@SCHED.scheduled_job('cron', day=1, hour=9, minute=30, second=0, timezone=MEXICO_CITY_TIMEZONE)
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

    # Extract info from pdfs
    tickets_info = []
    for link, email_id in links:
        try:
            tickets_info.extend(ADO.get_info_from_pdf_link(link, email_id))
        except PdfReadError:
            print("Unable to read ticket. Ticket is probably cancelled or changed")

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
        first_instant_of_current_month = current_date.replace(day=1,
                                                              hour=0,
                                                              minute=0,
                                                              second=0,
                                                              microsecond=0
                                                              )
        # From first instant of current month,
        # substract a microsecond to get the last instant of previous month
        last_instant_of_previous_month = first_instant_of_current_month - timedelta(microseconds=1)
        # From the last instant of that previous month,
        # go to the first instant of that previous month
        first_instant_of_previous_month = last_instant_of_previous_month.replace(day=1,
                                                                                 hour=0,
                                                                                 minute=0,
                                                                                 second=0,
                                                                                 microsecond=0
                                                                                 )

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
    if len(main_tickets) > 0:
        pdf_link = ADO.facturar_lote(main_tickets)

        # Send Telegram message
        text = "*Facturación detectada ADO*\n\n"
        # Check if facturación was successful
        if pdf_link is None:
            text += "Facturación fallida"
        else:
            text += f"*PDF Link*: {pdf_link}"
        # Send message
        send_telegram_message(text)

    if len(other_tickets) > 0:
        pdf_link = ADO.facturar_lote(other_tickets)

        # Send Telegram message
        text = "*Facturación detectada ADO*\n\n"
        # Check if facturación was successful
        if pdf_link is None:
            text += "Facturación fallida"
        else:
            text += f"*PDF Link*: {pdf_link}"
        # Send message
        send_telegram_message(text)

    # Get info fro
    print("Facturando ADO Done\n")

if __name__ == "__main__":
    initialize_firebase()
    SCHED.start()
