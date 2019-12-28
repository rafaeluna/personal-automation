import os
import requests
import firebase_admin
import re
import urllib
import io
import PyPDF2

from bs4 import BeautifulSoup
from firebase_admin import db, credentials
from pprint import pprint as pp
from apscheduler.schedulers.blocking import BlockingScheduler

sched = BlockingScheduler()

MS_GRAPH_URL = 'https://graph.microsoft.com/v1.0'
TELEGRAM_URL = 'https://api.telegram.org'

def initialize_firebase():
	cert = credentials.Certificate({
		'type': 'service_account',
		'token_uri': 'https://oauth2.googleapis.com/token',
		'private_key': os.getenv('PRIVATE_KEY').replace('\\n','\n'),
		'client_email': os.getenv('CLIENT_EMAIL')
	})
	firebase_admin.initialize_app(cert, {'databaseURL': os.getenv('DATABASE_URL')})

def get_token():
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
		'grant_type': 'authorization_code',
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
	headers = {
		'Authorization': token
	}
	response = requests.get(f'{MS_GRAPH_URL}/me/mailFolders/{folder_id}/messages', headers=headers)
	emails = response.json()['value']
	return emails

def process_UberEats(soup):
	amount = soup.find(text=re.compile(r'MX\$.+')).strip().replace('MX$','')
	description = 'Comida'

	return {
		'amount': amount,
		'description': description,
		'category': 'Comida',
		'payee': 'Uber Eats'
	}

def process_Uber(soup):
	amount = soup.find(text=re.compile(r'MX\$.+')).strip().replace('MX$','')
	return {
		'amount': amount,
		'description': 'Uber',
		'category': 'Taxi',
		'payee': 'Uber'
	}

def process_ADO(soup):
	# Get link to pdf
	link = soup.find('a', string=re.compile('Boleto'))['href']
	pdf = requests.get(link).content
	pdf_file = io.BytesIO(pdf)
	reader = PyPDF2.PdfFileReader(pdf_file)
	num_pages = reader.getNumPages()

	logs = []
	for page_number in range(num_pages):
		text = reader.getPage(page_number).extractText()
		amount = re.search(r'\$ (.+)PRECIO TOTAL', text).group(1)
		logs.append({
			'amount': amount,
			'description': 'ADO',
			'category': 'Deudas',
			'payee': 'ADO',
			'tag': 'Deudas'
		})

	return logs


def process_email(email):

	soup = BeautifulSoup(email['body']['content'], 'html.parser')
	sender = email['sender']['emailAddress']['name']
	subject = email['subject']

	print(f'Processing {subject}')

	# Handle Uber and Uber Eats
	if sender == 'Uber Receipts':
		if 'Uber Eats' in subject:
			log_data = process_UberEats(soup)
		else:
			log_data = process_Uber(soup)

	elif sender == 'ADO en Linea':
		log_data = process_ADO(soup)

	return log_data

def delete_emails_in_folder(emails, token, folder_id):
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

@sched.scheduled_job('interval', minutes=1)
def main():
	initialize_firebase()
	token = get_token()
	emails = gather_emails(token, os.getenv('DEBIT_AND_CREDIT_FOLDER_ID'))
	
	transactions = []
	for email in emails:
		transaction = process_email(email)
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
		response = requests.post(url, data=data)

	delete_emails_in_folder(emails, token, os.getenv('DEBIT_AND_CREDIT_FOLDER_ID'))


sched.start()