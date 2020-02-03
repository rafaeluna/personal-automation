'''
Module for processing facturas ADO
'''

import io
import re
from pprint import pprint as pp

import requests
import PyPDF2
from bs4 import BeautifulSoup


# Global vars
RFC = 'IVE950901EI6'
VALIDATE_URL = 'http://factura.grupoado.com.mx/jsp/validate.jsp'
REGISTER_URL = 'http://factura.grupoado.com.mx/register.jsp'
FACTURAR_URL = 'http://factura.grupoado.com.mx/facturar.jsp'

def get_info_from_pdf_link(link, email_id):
    '''
    Reads contents of PDF given a link
    '''
    print('Extracting info from pdf...')
    pdf = requests.get(link).content
    pdf_file = io.BytesIO(pdf)
    reader = PyPDF2.PdfFileReader(pdf_file)
    num_pages = reader.getNumPages()

    ticket_info = []
    for page_number in range(num_pages):
        text = reader.getPage(page_number).extractText()
        ticket_info.append({
            'folio': re.search(r'^\d+', text).group(0),
            'name': re.search(r'/NAME(.+)ORIGEN', text).group(1),
            'seat': re.search(r'SEAT(.+)FECHA', text).group(1),
            'price': re.search(r'\$ (.+)PRECIO', text).group(1),
            'date': re.search(r'/DATEADULTO[^\d]+(.+)HORA/HOUR', text).group(1),
            'email_id': email_id
        })

    for ticket in ticket_info:
        for k, i in ticket.items():
            print(f'\t{k}: {i}')

    return ticket_info


def facturar_lote(tickets):
    '''
    Factura boletos de ADO en lote o individuales
    '''

    print("Facturando lote...")

    # Start an http session
    session = requests.Session()
    # Validate all tickets together and obtain idlote
    id_lote = -1
    for ticket in tickets:
        response = session.post(VALIDATE_URL, data={
            'tipo': 'validateFolio',
            'folio': ticket['folio'],
            'asiento': ticket['seat'],
            'rfc': RFC,
            'idl': id_lote
        })
        id_lote = response.json()[0]['IDL']

    # Registrar factura
    register_data = {
        'sch_RFC': RFC,
        'idlote': id_lote,
        'rfc': RFC
    }
    register_data['sch_Id_Ticket'] = tickets[0]['folio'] if len(tickets) == 1 else ''
    register_data['sch_Ticket_Amount'] = tickets[0]['seat'] if len(tickets) == 1 else ''
    response = session.post(REGISTER_URL, data=register_data)

    # Scrape pre-existing data
    # Gather data from IDs
    soup = BeautifulSoup(response.text, 'html.parser')
    field_ids = [
        'RRfc',
        'IDDatosCliente',
        'RName',
        'RCalle',
        'RColonia',
        'RNumExt',
        'RNumInt',
        'RMunicipio',
        'RCodigoPostal',
        'RPais',
        'REmail'
    ]
    data = {}
    for field in field_ids:
        data[field] = soup.find(id=field)['value']
    # Find dynamic fields' data hidden in the javascript
    data['RNac'] = re.search(r'#RNac \[value="(.+)"\]', response.text).group(1)
    data['REstado'] = re.search(r'#REstado \[value="(.+)"\]', response.text).group(1)
    # Change names to match request
    data['id_datos_cliente'] = data['IDDatosCliente']
    data['idlo'] = id_lote
    # Hardset email to mine
    data['REmail'] = 'rafaelunaa@hotmail.com'
    # Delete unnecesary params
    del data['IDDatosCliente']

    pp(data)

    # CRITICAL PART!
    # Facturar
    response = session.post(FACTURAR_URL, data=data)

    if response.ok:
        print('Facturación exitosa')
        # Get download links in case email is not sent
        soup = BeautifulSoup(response.text, 'html.parser')
        pdf_js = soup.find(id='buttondwPDF').get('onclick')
        pdf_link = re.search(r'\(\'(.+)\'\)', pdf_js).group(1)
        return pdf_link

    print('Facturación fallida :-(')
    return None
