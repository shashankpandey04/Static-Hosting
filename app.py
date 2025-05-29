import os
import json
import shutil
# import tempfile (removed as it is unused)
import datetime
from flask import Flask, request, send_from_directory, abort, render_template, jsonify, redirect, session
import zipfile
from dotenv import load_dotenv
# import requests (removed as it is unused)
import git
# from werkzeug.utils import secure_filename (removed as it is unused)
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

UPLOAD_FOLDER = 'uploads'
SITE_FOLDER = 'user_sites'
GITHUB_FOLDER = 'github_repos'
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "super_secret_token")

# Create necessary directories
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(SITE_FOLDER, exist_ok=True)
os.makedirs(GITHUB_FOLDER, exist_ok=True)
os.makedirs(os.path.join(GITHUB_FOLDER, 'temp'), exist_ok=True)

# Data storage (in a production app, this would be a database)
SITES_DB_FILE = 'sites_db.json'
ACTIVITY_DB_FILE = 'activity_db.json'

# Initialize app
app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = os.getenv("SECRET_KEY", "super_secret_key_for_sessions")

# Helper function to load sites database
def load_sites_db():
    if os.path.exists(SITES_DB_FILE):
        with open(SITES_DB_FILE, 'r') as f:
            return json.load(f)
    return {'sites': []}

# Helper function to save sites database
def save_sites_db(db):
    with open(SITES_DB_FILE, 'w') as f:
        json.dump(db, f)

# Helper function to add activity
def add_activity(site, type_activity, status):
    if os.path.exists(ACTIVITY_DB_FILE):
        with open(ACTIVITY_DB_FILE, 'r') as f:
            activities = json.load(f)
    else:
        activities = {'activities': []}
    
    activities['activities'].append({
        'site': site,
        'type': type_activity,
        'date': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'status': status
    })
    
    # Limit to 50 recent activities
    if len(activities['activities']) > 50:
        activities['activities'] = activities['activities'][-50:]
    
    with open(ACTIVITY_DB_FILE, 'w') as f:
        json.dump(activities, f)

# Authentication middleware
from functools import wraps

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_authenticated'):
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated_function

# Landing page
@app.route('/')
def landing_page():
    return render_template('landing.html')

# Admin Login UI
@app.route('/login')
def login():
    return render_template('admin/login.html')

# Admin Authentication
@app.route('/admin/authenticate', methods=['POST'])
def authenticate():
    if request.is_json:
        data = request.get_json()
        token = data.get('token')
    else:
        token = request.form.get('token')
    
    if token == ADMIN_TOKEN:
        session['admin_authenticated'] = True
        return jsonify({'success': True, 'redirect': '/admin/dashboard'})
    else:
        return jsonify({'success': False, 'message': 'Invalid token'}), 401

# Logout
@app.route('/logout')
def logout():
    session.pop('admin_authenticated', None)
    return redirect('/')

# Admin Dashboard
@app.route('/admin/dashboard')
@admin_required
def dashboard():
    return render_template('admin/dashboard.html')

# Get dashboard data
@app.route('/api/dashboard')
@admin_required
def get_dashboard_data():
    sites_db = load_sites_db()
    sites = sites_db.get('sites', [])
    
    github_deployments = sum(1 for site in sites if site.get('type') == 'github')
    zip_uploads = sum(1 for site in sites if site.get('type') == 'zip')
    
    # Get recent activities
    activities = []
    if os.path.exists(ACTIVITY_DB_FILE):
        with open(ACTIVITY_DB_FILE, 'r') as f:
            activity_data = json.load(f)
            activities = activity_data.get('activities', [])[-5:]  # Get the 10 most recent
    
    return jsonify({
        'total_sites': len(sites),
        'github_deployments': github_deployments,
        'zip_uploads': zip_uploads,
        'recent_activity': activities
    })

# Get all sites
@app.route('/api/sites')
@admin_required
def get_sites():
    sites_db = load_sites_db()
    return jsonify(sites_db)

# Delete a site
@app.route('/api/site/<domain>', methods=['DELETE'])
@admin_required
def delete_site(domain):
    sites_db = load_sites_db()
    folder_name = domain.replace('.', '_')
    site_path = os.path.join(SITE_FOLDER, folder_name)
    
    # Remove from sites list
    sites_db['sites'] = [site for site in sites_db['sites'] if site.get('domain') != domain]
    save_sites_db(sites_db)
    
    # Remove site files
    if os.path.exists(site_path):
        try:
            shutil.rmtree(site_path)
            add_activity(domain, 'delete', 'success')
            return jsonify({'success': True, 'message': f'Site {domain} deleted successfully'})
        except Exception as e:
            add_activity(domain, 'delete', 'failed')
            return jsonify({'success': False, 'message': f'Error deleting site: {str(e)}'}), 500
    else:
        return jsonify({'success': False, 'message': 'Site not found'}), 404

# Get GitHub sites
@app.route('/api/github-sites')
@admin_required
def get_github_sites():
    sites_db = load_sites_db()
    github_sites = [site for site in sites_db.get('sites', []) if site.get('type') == 'github']
    return jsonify({'sites': github_sites})

# Deploy from GitHub
@app.route('/api/github-deploy', methods=['POST'])
@admin_required
def github_deploy():
    data = request.get_json()
    domain = data.get('domain')
    repo_url = data.get('repo_url')
    branch = data.get('branch', 'main')
    directory = data.get('directory', '')
    
    if not domain or not repo_url:
        return jsonify({'success': False, 'message': 'Missing domain or repository URL'}), 400
    
    folder_name = domain.replace('.', '_')
    site_path = os.path.join(SITE_FOLDER, folder_name)
    temp_repo_path = os.path.join(GITHUB_FOLDER, 'temp', folder_name)
    
    # Ensure directories exist
    os.makedirs(site_path, exist_ok=True)
    
    try:
        # Clean up any existing temporary repository directory first
        if os.path.exists(temp_repo_path):
            logger.info(f"Removing existing temporary repository directory: {temp_repo_path}")
            shutil.rmtree(temp_repo_path)
        
        # Clone the repository - had to remove the double cloning that was happening before
        logger.info(f"Cloning repository {repo_url} (branch {branch}) to {temp_repo_path}")
        repo = git.Repo.clone_from(repo_url, temp_repo_path, branch=branch)
        
        # If a specific directory is specified, copy from there
        source_dir = os.path.join(temp_repo_path, directory) if directory else temp_repo_path
        
        if not os.path.exists(source_dir):
            raise Exception(f"Directory '{directory}' not found in repository")
        
        # Clear existing site files
        logger.info(f"Clearing existing site files in {site_path}")
        for item in os.listdir(site_path):
            item_path = os.path.join(site_path, item)
            if os.path.isdir(item_path):
                shutil.rmtree(item_path)
            else:
                os.remove(item_path)
        
        # Copy files from repository to site directory
        logger.info(f"Copying files from {source_dir} to {site_path}")
        for item in os.listdir(source_dir):
            src = os.path.join(source_dir, item)
            dst = os.path.join(site_path, item)
            if item == '.git':  # Skip .git directory
                continue
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        
        # Update sites database
        sites_db = load_sites_db()
        existing_site = next((site for site in sites_db.get('sites', []) if site.get('domain') == domain), None)
        
        if existing_site:
            existing_site.update({
                'repo_url': repo_url,
                'branch': branch,
                'directory': directory,
                'updated_at': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
        else:
            sites_db.setdefault('sites', []).append({
                'domain': domain,
                'type': 'github',
                'repo_url': repo_url,
                'branch': branch,
                'directory': directory,
                'created_at': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'updated_at': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
        
        save_sites_db(sites_db)
        add_activity(domain, 'github-deploy', 'success')
        
        return jsonify({'success': True, 'message': f'Site {domain} deployed successfully from GitHub'})
    
    except Exception as e:
        logger.error(f"GitHub deployment error: {str(e)}")
        add_activity(domain, 'github-deploy', 'failed')
        return jsonify({'success': False, 'message': f'Error deploying from GitHub: {str(e)}'}), 500
    finally:
        # Clean up
        if os.path.exists(temp_repo_path):
            try:
                logger.info(f"Cleaning up temporary repository directory: {temp_repo_path}")
                shutil.rmtree(temp_repo_path)
            except Exception as cleanup_error:
                logger.warning(f"Failed to clean up temporary repository: {str(cleanup_error)}")

# Redeploy GitHub site
@app.route('/api/github-deploy/<domain>', methods=['POST'])
@admin_required
def redeploy_github_site(domain):
    sites_db = load_sites_db()
    site = next((site for site in sites_db.get('sites', []) if site.get('domain') == domain and site.get('type') == 'github'), None)
    
    if not site:
        return jsonify({'success': False, 'message': 'GitHub site not found'}), 404
    
    # Create a request body with the site's existing configuration
    data = {
        'domain': domain,
        'repo_url': site.get('repo_url'),
        'branch': site.get('branch', 'main'),
        'directory': site.get('directory', '')
    }
    
    # Use the flask request context to simulate a new request
    with app.test_request_context(
        '/api/github-deploy', 
        method='POST',
        json=data
    ):
        return github_deploy()

# Delete GitHub site
@app.route('/api/github-site/<domain>', methods=['DELETE'])
@admin_required
def delete_github_site(domain):
    return delete_site(domain)

# Admin Upload Endpoint
@app.route('/upload', methods=['POST'])
def upload_zip():
    domain = request.form.get('domain')
    token = request.form.get('token', '')
    zipfile_obj = request.files.get('zipfile')

    # Check authentication - either through session or token
    if not session.get('admin_authenticated') and token != ADMIN_TOKEN:
        return "403 Forbidden", 403
        
    if not domain or not zipfile_obj:
        return "Missing domain or file", 400

    folder_name = domain.replace('.', '_')
    site_path = os.path.join(SITE_FOLDER, folder_name)
    
    # Ensure the directory exists
    os.makedirs(site_path, exist_ok=True)
    
    # Save the zip file temporarily
    zip_path = os.path.join(UPLOAD_FOLDER, f"{folder_name}.zip")
    zipfile_obj.save(zip_path)
    
    try:
        # Extract directly to the site path, not to a subfolder
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            # Get the list of files to check for a common root directory
            file_list = zip_ref.namelist()
            
            # Check if all files are in a single root folder
            common_prefix = os.path.commonprefix(file_list)
            if common_prefix and common_prefix.endswith('/') and common_prefix.count('/') == 1:
                # Files are in a single root directory, extract with members to strip the root
                for member in zip_ref.infolist():
                    if member.filename.startswith(common_prefix):
                        # Extract without the common prefix
                        extracted_path = member.filename[len(common_prefix):]
                        if extracted_path:  # Skip the root directory itself
                            if member.is_dir():
                                os.makedirs(os.path.join(site_path, extracted_path), exist_ok=True)
                            else:
                                with zip_ref.open(member) as source, \
                                     open(os.path.join(site_path, extracted_path), 'wb') as target:
                                    target.write(source.read())
            else:
                # No common root or multiple roots, extract directly
                zip_ref.extractall(site_path)
        
        # Update sites database
        sites_db = load_sites_db()
        existing_site = next((site for site in sites_db.get('sites', []) if site.get('domain') == domain), None)
        
        if existing_site:
            existing_site.update({
                'updated_at': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
        else:
            sites_db.setdefault('sites', []).append({
                'domain': domain,
                'type': 'zip',
                'created_at': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'updated_at': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
        
        save_sites_db(sites_db)
        add_activity(domain, 'zip-upload', 'success')
                
        # Clean up the temporary zip file
        os.remove(zip_path)
        
        return f"Website for {domain} deployed successfully!"
    except Exception as e:
        # Clean up on error
        if os.path.exists(zip_path):
            os.remove(zip_path)
        add_activity(domain, 'zip-upload', 'failed')
        return f"Error extracting ZIP: {str(e)}", 500

# Function to check if a path is a valid site folder and return the right file
def serve_site_content(site_path, path):
    if not os.path.isdir(site_path):
        return abort(404)
    
    file_path = os.path.join(site_path, path)
    if os.path.isfile(file_path):
        return send_from_directory(site_path, path)
    elif os.path.isdir(os.path.join(site_path, path)):
        # Check if index.html exists in directory
        index_path = os.path.join(path, 'index.html')
        if os.path.isfile(os.path.join(site_path, index_path)):
            return send_from_directory(site_path, index_path)
    return abort(404)

# Direct site access through root URL (e.g., /something_xyz/)
@app.route('/<folder_name>/', defaults={'path': 'index.html'})
@app.route('/<folder_name>/<path:path>')
def serve_direct(folder_name, path):
    # Special routes that should not be treated as site paths
    if folder_name in ['login', 'admin', 'upload', 'static', 'api', 'logout']:
        return abort(404)
    
    # Don't handle 'sites' and 'site' prefixes here
    if folder_name in ['sites', 'site']:
        return abort(404)
    
    # Check if this is an actual site folder
    site_path = os.path.join(SITE_FOLDER, folder_name)
    return serve_site_content(site_path, path)

# Serve Static Files by Domain (original domain-based routing)
@app.route('/site/<domain>/', defaults={'path': 'index.html'})
@app.route('/site/<domain>/<path:path>')
def serve_site_by_domain(domain, path):
    folder_name = domain.replace('.', '_')
    site_path = os.path.join(SITE_FOLDER, folder_name)
    return serve_site_content(site_path, path)

# Serve Static Files by Path (for local testing)
@app.route('/sites/<folder_name>/', defaults={'path': 'index.html'})
@app.route('/sites/<folder_name>/<path:path>')
def serve_static_by_path(folder_name, path):
    site_path = os.path.join(SITE_FOLDER, folder_name)
    return serve_site_content(site_path, path)

# Debug route to list available sites
@app.route('/debug/sites')
def debug_sites():
    try:
        sites = os.listdir(SITE_FOLDER)
        return {
            'sites': sites,
            'site_folder': os.path.abspath(SITE_FOLDER),
            'exists': os.path.exists(SITE_FOLDER),
            'is_dir': os.path.isdir(SITE_FOLDER)
        }
    except Exception as e:
        return {'error': str(e)}

# Fix: URL value preprocessor must accept endpoint and values_dict parameters
@app.url_value_preprocessor
def get_site_from_host(endpoint, values_dict):
    # Only process for domain-specific endpoints
    if endpoint not in ('domain_root', 'domain_path'):
        return
        
    # Extract the host from the request
    host = request.host.split(':')[0]
    
    # Don't process localhost or 127.0.0.1
    if host in ('localhost', '127.0.0.1'):
        return
    
    # Add the domain to the values dictionary if it's not already there
    if values_dict is not None and 'domain' not in values_dict:
        values_dict['domain'] = host

# Handler for domain-based routes
@app.route('/', defaults={'path': 'index.html'}, endpoint='domain_root')
@app.route('/<path:path>', endpoint='domain_path')
def serve_static(path):
    domain = request.host.split(':')[0]
    
    # If on localhost, handle it differently
    if domain in ('localhost', '127.0.0.1'):
        # For root path, show landing page
        if path == 'index.html':
            return landing_page()
        # Otherwise, this will likely be caught by other routes or return 404
        return abort(404)
    
    # This is a custom domain request, so serve the site content
    folder_name = domain.replace('.', '_')
    site_path = os.path.join(SITE_FOLDER, folder_name)

    file_path = os.path.join(site_path, path)
    if os.path.isfile(file_path):
        return send_from_directory(site_path, path)
    elif os.path.isdir(os.path.join(site_path, path)):
        # Check if index.html exists in directory
        index_path = os.path.join(path, 'index.html')
        if os.path.isfile(os.path.join(site_path, index_path)):
            return send_from_directory(site_path, index_path)
    return abort(404)

if __name__ == '__main__':
    # Make sure to run with debug=True to see detailed error messages
    app.run(debug=True, port=80, host='0.0.0.0')