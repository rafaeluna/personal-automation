'''
Module for processing D&C emails and transactions
'''

import re
import io

import requests
import PyPDF2
from bs4 import BeautifulSoup

def process_uber_eats(soup):
    '''
    Process the soup of an uber eats email, searching for transaction data
    '''
    amount = soup.find(text=re.compile(r"MX\$.+")).strip().replace("MX$", "")
    description = "Comida"

    return {
        "amount": amount,
        "description": description,
        "category": "Comida",
        "payee": "Uber Eats"
    }

def process_uber(soup):
    '''
    Process the soup of an uber email, searching for transaction data
    '''
    amount = soup.find(text=re.compile(r"MX\$.+")).strip().replace("MX$", "")
    return {
        "amount": amount,
        "description": "Uber",
        "category": "Taxi",
        "payee": "Uber"
    }

def process_ado(soup):
    '''
    Processes the soup of an ADO email, searching for transaction data within it"s PDF
    '''
    # Get link to pdf
    link = soup.find("a", string=re.compile("Boleto"))["href"]
    pdf = requests.get(link).content
    pdf_file = io.BytesIO(pdf)
    reader = PyPDF2.PdfFileReader(pdf_file)
    num_pages = reader.getNumPages()

    amount = 0
    for page_number in range(num_pages):
        text = reader.getPage(page_number).extractText()
        amount += float(re.search(r"\$ (.+)PRECIO TOTAL", text).group(1))

    return {
        "amount": amount,
        "description": "ADO",
        "category": "Deudas",
        "payee": "ADO",
        "tag": "Deudas"
    }


def process_parkimovil(soup):
    '''
    Processes soup for Parkimovil email
    '''

    # Get ammount
    # Get tag with string "Total:"
    total_tag = soup.find("strong", string=re.compile("Total:"))
    # From that tag, go to its parent and get content at index 3, total price should be there
    total_price_string = total_tag.parent.contents[3]
    # Get just the digits
    amount = total_price_string.replace("MX$", "")

    # Get parking location by finding tag with "le agradece su visita."
    visit_place = re.search(r"<strong>(.+)</strong>\s*le agradece su visita\.", str(soup)).group(1)

    return {
        "amount": amount,
        "description": "Estacionamiento",
        "category": "Servicios",
        "payee": "Parkimovil",
        "notes": f"Lugar: {visit_place}"
    }


def process_email(email):
    '''
    Generic process email for transaction.
    Depending on sender and subject, sends email"s html in soup form to corresponding method
    '''
    soup = BeautifulSoup(email["body"]["content"], "html.parser")
    sender = email["sender"]["emailAddress"]["name"]
    subject = email["subject"]

    print(f"Processing {subject}")

    # Handle Uber and Uber Eats
    if sender == "Uber Receipts":
        if "Uber Eats" in subject:
            log_data = process_uber_eats(soup)
        else:
            log_data = process_uber(soup)

    elif sender == "ADO en Linea":
        log_data = process_ado(soup)

    elif sender == "Parkimovil":
        log_data = process_parkimovil(soup)

    else:
        print(f"No rule for sender '{sender}' with subject '{subject}', skipping...")
        raise Exception(f"No rule for sender '{sender}' with subject '{subject}'")

    return log_data
