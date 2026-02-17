import os
import json
import pickle
import io  # <--- CRITICAL: Was missing
from flask import Flask, request, send_from_directory, send_file, \
    render_template, redirect, url_for, abort
# from tinydb import TinyDB
from functools import wraps
from io import BytesIO, StringIO
import datetime

from dotenv import load_dotenv

try:
    # 1. Load Environment Variables
    # Pointing to your specific path
    env_path = "/Users/lucashueda/Documents/Doutorado/github/eval_mushra/.env.local"
    load_dotenv(dotenv_path=env_path)
except:
    print("not loading local env")

# Import your local modules (ensure these .py files are in the same folder)
def json_to_dict(payload):
    """ Transform webMUSHRA JSON dict to sane structure

    Parameters
    ----------
    payload : dict_like
        The container to be transformed

    Returns
    -------
    d : dict_like
        The transformed container

    Notes
    -----

    Actions taken:

    1. One dataset per trial is generated
    2. Config from global payload is inserted into all datasets
    3. TestId from global payload is inserted into all datasets
    4. date is added to all datasets
    5. Questionaire structure

        .. code-block:: python

            {'name': ['firstname', 'age'], 'response': ['Nils', 29]}

        becomes

        .. code-block:: python

            {'firstname': 'Nils', 'age': 29}

    6. UUID4 field is added to questionaire

    """
    questionaire = payload['participant']
    questionaire = dict(
        zip(questionaire['name'], questionaire['response'])
    )
    questionaire['uuid'] = str(uuid.uuid4())
    insert = []

    for trial in payload['trials']:
        data = trial

        data['config'] = payload['config']
        data['testId'] = payload['testId']
        data['date'] = str(datetime.datetime.now())
        data['questionaire'] = questionaire

        insert.append(data)

    return insert

app = Flask(__name__)

# --- CONFIGURATION ---

# In app.py
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEBMUSHRA_DIR = os.path.join(BASE_DIR, 'webMUSHRA')
# WEBMUSHRA_DIR = "/Users/lucashueda/Documents/Doutorado/github/webMUSHRA"
DB_PATH = os.path.join(os.getcwd(), "db/webmushra.json")
ADMIN_ALLOWLIST = ["127.0.0.1", "localhost"]

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from google.oauth2 import service_account

# --- CONFIGURATION ---
# SERVICE_ACCOUNT_FILE = '/Users/lucashueda/Documents/Doutorado/github/pymushra/service_account.json'

# In app.py
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_ACCOUNT_FILE = os.path.join(BASE_DIR, 'service_account.json')


GOOGLE_SHEET_NAME = "webMUSHRA_Results" # The name of the spreadsheet file

# The ID of the folder in Google Drive where JSON files will be stored
DRIVE_FOLDER_ID = '19vAvkplyvb1yH27bP7X_goEh9KGncoeJ' 
SCOPES = ['https://www.googleapis.com/auth/drive']

app.config['webmushra_dir'] = WEBMUSHRA_DIR
app.config['admin_allowlist'] = ADMIN_ALLOWLIST
# Initialize TinyDB


# Manual directory creation for TinyDB 3.x compatibility
# db_dir = os.path.dirname(DB_PATH)
# if not os.path.exists(db_dir):
#     os.makedirs(db_dir)
# db_instance = TinyDB(DB_PATH)


app.config['db'] = None


# --- ROUTES ---

@app.route('/')
@app.route('/<path:url>')
def home(url='index.html'):
    return send_from_directory(app.config['webmushra_dir'], url)

def find_file(service, name):
    """Find a file by name within the specific folder."""
    query = f"name = '{name}' and '{DRIVE_FOLDER_ID}' in parents and trashed = false"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    files = results.get('files', [])
    return files[0] if files else None

def download_json(service, file_id):
    """Download and parse an existing JSON file from Drive."""
    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    return json.loads(fh.getvalue().decode('utf-8'))

def upload_json(service, name, data, file_id=None):
    """Upload or update a JSON file using the parent folder's quota."""
    file_metadata = {
        'name': name, 
        'parents': [DRIVE_FOLDER_ID]
    }
    
    # We use a stream to upload the JSON data
    media = MediaIoBaseUpload(
        io.BytesIO(json.dumps(data, indent=4).encode('utf-8')),
        mimetype='application/json',
        resumable=True
    )
    
    if file_id:
        # Update existing file
        service.files().update(
            fileId=file_id, 
            media_body=media,
            supportsAllDrives=True # Required for service account uploads
        ).execute()
    else:
        # Create new file
        service.files().create(
            body=file_metadata, 
            media_body=media, 
            fields='id',
            supportsAllDrives=True # Required for service account uploads
        ).execute()

import json

def get_sheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    service_account_info = json.loads(os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON'))
    
    print(service_account_info)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(service_account_info, scope)
    client = gspread.authorize(creds)
    return client.open(GOOGLE_SHEET_NAME)

import uuid

@app.route('/service/write.php', methods=['POST'])
@app.route('/collect', methods=['POST'])
def collect():
    print("Collect request received!")
    session_json = request.form.get('sessionJSON')
    
    if session_json:
        try:
            payload = json.loads(session_json)
            # WebMUSHRA data is deeply nested, let's flatten it for the sheet
            # We use your existing casting module
            flat_data = json_to_dict(payload) 
            
            original_id = payload['trials'][0].get('testId', 'default_test')
            test_id = f"{original_id}_{str(uuid.uuid4())[:4]}"

            client_sheet = get_sheet()
            
            # Try to find the tab for this test, create it if missing
            try:
                worksheet = client_sheet.worksheet(test_id)
            except gspread.exceptions.WorksheetNotFound:
                # Create worksheet with headers from the first data row
                headers = list(flat_data[0].keys())
                worksheet = client_sheet.add_worksheet(title=test_id, rows="100", cols=len(headers))
                worksheet.append_row(headers)

            # Add the results
            for row_dict in flat_data:
                # Convert list/dict values to strings so Sheets accepts them
                row_values = [str(v) if isinstance(v, (list, dict)) else v for v in row_dict.values()]
                worksheet.append_row(row_values)

            print(f"Successfully saved to Sheet: {test_id}")
            return {'error': False, 'message': "Saved to Google Sheets"}

        except Exception as e:
            print(f"Error: {str(e)}")
            return {'error': True, 'message': str(e)}
            
    return "400 Bad Request", 400

# --- EXECUTION ---
if __name__ == '__main__':
    # Use debug=True for development
    app.run(debug=True, host='0.0.0.0', port=5000)