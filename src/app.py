import os
import json
import pickle
import io  # <--- CRITICAL: Was missing
from flask import Flask, request, send_from_directory, send_file, \
    render_template, redirect, url_for, abort
from tinyrecord import transaction
from tinydb import TinyDB
from functools import wraps
from io import BytesIO, StringIO

# Import your local modules (ensure these .py files are in the same folder)
from . import stats, casting, utils

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
db_dir = os.path.dirname(DB_PATH)
if not os.path.exists(db_dir):
    os.makedirs(db_dir)
db_instance = TinyDB(DB_PATH)


app.config['db'] = db_instance

# --- DECORATORS ---
def only_admin_allowlist(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if request.remote_addr in app.config['admin_allowlist']:
            return f(*args, **kwargs)
        return abort(403)
    return wrapped

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

def get_sheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_FILE, scope)
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
            flat_data = casting.json_to_dict(payload) 
            
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

@app.route('/admin/')
@app.route('/admin/list')
@only_admin_allowlist
def admin_list():
    db = app.config['db']
    collection_names = db.tables()
    
    collections = []
    for name in collection_names:
        df = casting.collection_to_df(db.table(name))
        if len(df) > 0:
            collections.append({
                'id': name,
                'participants': len(df[('questionaire', 'uuid')].unique()),
                'last_submission': df[('wm', 'date')].max(),
            })

    configs = utils.get_configs(os.path.join(app.config['webmushra_dir'], "configs"))
    return render_template("admin/list.html", collections=collections, configs=configs)


@app.route('/admin/delete/<testid>/')
@only_admin_allowlist
def admin_delete(testid):
    app.config['db'].drop_table(testid)
    return redirect(url_for('admin_list'))


@app.route('/admin/info/<testid>/')
@only_admin_allowlist
def admin_info(testid):
    collection = app.config['db'].table(testid)
    df = casting.collection_to_df(collection)
    try:
        configs = df['wm']['config'].unique().tolist()
    except KeyError:
        configs = []

    configs = map(os.path.basename, configs)

    return render_template(
        "admin/info.html",
        testId=testid,
        configs=configs
    )


@app.route('/admin/latest/<testid>/')
@only_admin_allowlist
def admin_latest(testid):
    collection = app.config['db'].table(testid)
    latest = sorted(collection.all(), key=lambda x: x['date'], reverse=True)[0]
    return latest


@app.route('/admin/stats/<testid>/<stats_type>')
@only_admin_allowlist
def admin_stats(testid, stats_type='mushra'):
    collection = app.config['db'].table(testid)
    df = casting.collection_to_df(collection)
    df.columns = utils.flatten_columns(df.columns)
    # analyse mushra experiment
    try:
        if stats_type == "mushra":
            return stats.render_mushra(testid, df)
    except ValueError as e:
        return render_template(
            'error/error.html', type="Value", message=str(e)
        )
    return render_template('error/404.html'), 404

@app.route(
    '/admin/download/<testid>.<filetype>',
    defaults={'show_as': 'download'})
@app.route(
    '/admin/download/<testid>/<statstype>.<filetype>',
    defaults={'show_as': 'download'})
@app.route(
    '/download/<testid>/<statstype>.<filetype>',
    defaults={'show_as': 'download'})
@app.route(
    '/download/<testid>.<filetype>',
    defaults={'show_as': 'download'})
@app.route(
    '/admin/show/<testid>.<filetype>',
    defaults={'show_as': 'text'})
@app.route(
    '/admin/show/<testid>/<statstype>.<filetype>',
    defaults={'show_as': 'text'})
@only_admin_allowlist
def download(testid, show_as, statstype=None, filetype='csv'):
    allowed_types = ('csv', 'pickle', 'json', 'html')

    if show_as == 'download':
        as_attachment = True
    else:
        as_attachment = False

    if filetype not in allowed_types:
        return render_template(
            'error/error.html',
            type="Value",
            message="File type must be in %s" % ','.join(allowed_types)
        )

    if filetype == "pickle" and not as_attachment:
        return render_template(
            'error/error.html',
            type="Value",
            message="Pickle data cannot be viewed"
        )

    collection = app.config['db'].table(testid)
    df = casting.collection_to_df(collection)

    if statstype is not None:
        # subset by statstype
        df = df[df[('wm', 'type')] == statstype]

    # Merge hierarchical columns
    if filetype not in ("pickle", "html"):
        df.columns = utils.flatten_columns(df.columns.values)

    if len(df) == 0:
        return render_template(
            'error/error.html',
            type="Value",
            message="Data Frame was empty"
        )

    if filetype == "csv":
        # We need to escape certain objects in the DF to prevent Segfaults
        mem = StringIO()
        casting.escape_objects(df).to_csv(
            mem,
            sep=";",
            index=False,
            encoding='utf-8'
        )

    elif filetype == "html":
        mem = StringIO()
        df.sort_index(axis=1).to_html(mem, classes="table table-striped")

    elif filetype == "pickle":
        mem = BytesIO()
        pickle.dump(df, mem)

    elif filetype == "json":
        mem = StringIO()
        # We need to escape certain objects in the DF to prevent Segfaults
        casting.escape_objects(df).to_json(mem, orient='records')

    mem.seek(0)

    if (as_attachment or filetype != "html") and not isinstance(mem, BytesIO):
        mem2 = BytesIO()
        mem2.write(mem.getvalue().encode('utf-8'))
        mem2.seek(0)
        mem = mem2

    if as_attachment:
        return send_file(
            mem,
            download_name="%s.%s" % (testid, filetype),
            as_attachment=True,
            max_age=-1
        )
    else:
        if filetype == "html":
            return render_template('admin/table.html', table=mem.getvalue())
        else:
            return send_file(
                mem,
                mimetype="text/plain",
                cache_timeout=-1
            )



@app.context_processor
def utility_processor():
    def significance_stars(p, alpha=0.05):
        return ''.join(['<span class="glyphicon glyphicon-star small"></span>'] * stats.significance_class(p, alpha))
    return dict(significance_stars=significance_stars)

@app.template_filter('datetime')
def datetime_filter(value, format='%x %X'):
    return value.strftime(format)

# --- EXECUTION ---
if __name__ == '__main__':
    # Use debug=True for development
    app.run(debug=True, host='0.0.0.0', port=5000)