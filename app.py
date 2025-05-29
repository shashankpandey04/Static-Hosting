import os
import shutil
import datetime
from flask import Flask, request, send_from_directory, abort, render_template, jsonify, redirect, session
import zipfile
from dotenv import load_dotenv
import git
import logging
from pymongo import MongoClient
from bson import ObjectId
import time
import stat
import os.path
from ctypes import windll, c_uint32  # For Windows-specific deletion workaround
from github_helper import clone_github_repo_via_zip

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

UPLOAD_FOLDER = 'uploads'
SITE_FOLDER = 'user_sites'
GITHUB_FOLDER = 'github_repos'
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "super_secret_token")

# MongoDB configuration
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = os.getenv("DB_NAME", "foundev_hosting")

# Create necessary directories
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(SITE_FOLDER, exist_ok=True)
os.makedirs(GITHUB_FOLDER, exist_ok=True)
os.makedirs(os.path.join(GITHUB_FOLDER, 'temp'), exist_ok=True)

# Initialize app
app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = os.getenv("SECRET_KEY", "super_secret_key_for_sessions")

# Initialize MongoDB connection
mongo_client = MongoClient(MONGO_URI)
db = mongo_client[DB_NAME]
sites_collection = db['sites']
activities_collection = db['activities']

# Helper function to get all sites
def get_all_sites():
    return list(sites_collection.find())

# Helper function to get a site by domain
def get_site_by_domain(domain):
    return sites_collection.find_one({'domain': domain})

# Helper function to insert or update a site
def upsert_site(domain, data):
    return sites_collection.update_one(
        {'domain': domain},
        {'$set': data},
        upsert=True
    )

# Helper function to delete a site
def delete_site_from_db(domain):
    return sites_collection.delete_one({'domain': domain})

# Helper function to add activity
def add_activity(site, type_activity, status):
    activity = {
        'site': site,
        'type': type_activity,
        'date': datetime.datetime.now().isoformat(),
        'status': status
    }
    
    activities_collection.insert_one(activity)
    
    # Keep only the latest 50 activities
    total = activities_collection.count_documents({})
    if total > 50:
        # Get the oldest activities to remove
        to_delete = total - 50
        oldest = activities_collection.find().sort('date', 1).limit(to_delete)
        oldest_ids = [doc['_id'] for doc in oldest]
        activities_collection.delete_many({'_id': {'$in': oldest_ids}})

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
    sites = list(sites_collection.find())
    
    github_deployments = sum(1 for site in sites if site.get('type') == 'github')
    zip_uploads = sum(1 for site in sites if site.get('type') == 'zip')
    
    # Get recent activities
    recent_activities = list(activities_collection.find().sort('date', -1).limit(5))
    
    # Convert ObjectId to string for JSON serialization
    for activity in recent_activities:
        activity['_id'] = str(activity['_id'])
    
    return jsonify({
        'total_sites': len(sites),
        'github_deployments': github_deployments,
        'zip_uploads': zip_uploads,
        'recent_activity': recent_activities
    })

# Get all sites
@app.route('/api/sites')
@admin_required
def get_sites():
    sites = list(sites_collection.find())
    
    # Convert ObjectId to string for JSON serialization
    for site in sites:
        site['_id'] = str(site['_id'])
    
    return jsonify({'sites': sites})

# Delete a site
@app.route('/api/site/<domain>', methods=['DELETE'])
@admin_required
def delete_site(domain):
    folder_name = domain.replace('.', '_')
    site_path = os.path.join(SITE_FOLDER, folder_name)
    
    # Remove from database
    result = delete_site_from_db(domain)
    
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
    github_sites = list(sites_collection.find({'type': 'github'}))
    
    # Convert ObjectId to string for JSON serialization
    for site in github_sites:
        site['_id'] = str(site['_id'])
    
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
            safe_rmtree(temp_repo_path)
            
            # Verify the directory is gone
            if os.path.exists(temp_repo_path):
                logger.warning(f"Directory still exists after cleanup, creating a new temp path")
                # Create a new temp path with timestamp if we couldn't remove the old one
                timestamp = int(time.time())
                temp_repo_path = os.path.join(GITHUB_FOLDER, 'temp', f"{folder_name}_{timestamp}")
                os.makedirs(temp_repo_path, exist_ok=True)
        
        # Clone the repository with explicit garbage collection to reduce file locking
        logger.info(f"Cloning repository {repo_url} (branch {branch}) to {temp_repo_path}")
        
        # Use environment variables to configure git to avoid file locking
        git_env = os.environ.copy()
        git_env["GIT_TERMINAL_PROMPT"] = "0"  # Disable prompting
        git_env["GIT_FLUSH"] = "1"  # Flush more aggressively
        
        # Use our GitHub helper to download via ZIP API
        clone_github_repo_via_zip(repo_url, branch, temp_repo_path)
        
        # If a specific directory is specified, copy from there
        source_dir = os.path.join(temp_repo_path, directory) if directory else temp_repo_path
        
        if not os.path.exists(source_dir):
            raise Exception(f"Directory '{directory}' not found in repository")
        
        # Clear existing site files
        logger.info(f"Clearing existing site files in {site_path}")
        for item in os.listdir(site_path):
            item_path = os.path.join(site_path, item)
            if os.path.isdir(item_path):
                safe_rmtree(item_path)
            else:
                try:
                    os.remove(item_path)
                except Exception as e:
                    logger.warning(f"Failed to delete file {item_path}: {str(e)}")
        
        # Copy files from repository to site directory
        logger.info(f"Copying files from {source_dir} to {site_path}")
        files_copied = 0
        for item in os.listdir(source_dir):
            if item == '.git':  # Skip .git directory
                continue
                
            src = os.path.join(source_dir, item)
            dst = os.path.join(site_path, item)
            
            try:
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
                files_copied += 1
            except Exception as e:
                logger.warning(f"Error copying {src} to {dst}: {str(e)}")
        
        logger.info(f"Copied {files_copied} files/directories to {site_path}")
        
        # Release repo resources to avoid file locking
        if 'repo' in locals():
            repo.git.clear_cache()
            repo.close()
            del repo
        
        # Update site in database
        now = datetime.datetime.now().isoformat()
        site_data = {
            'domain': domain,
            'type': 'github',
            'repo_url': repo_url,
            'branch': branch,
            'directory': directory,
            'updated_at': now
        }
        
        existing_site = get_site_by_domain(domain)
        if not existing_site:
            site_data['created_at'] = now
            
        upsert_site(domain, site_data)
        add_activity(domain, 'github-deploy', 'success')
        
        return jsonify({'success': True, 'message': f'Site {domain} deployed successfully from GitHub'})
    
    except Exception as e:
        logger.error(f"GitHub deployment error: {str(e)}")
        add_activity(domain, 'github-deploy', 'failed')
        return jsonify({'success': False, 'message': f'Error deploying from GitHub: {str(e)}'}), 500
    finally:
        # Clean up - try to remove the temp directory with our safe method
        if os.path.exists(temp_repo_path):
            try:
                logger.info(f"Cleaning up temporary repository directory: {temp_repo_path}")
                # Force Python's garbage collection to release any file handles
                import gc
                gc.collect()
                
                # Wait a moment for any file handles to be released
                time.sleep(1)
                
                # Try to delete the directory
                safe_rmtree(temp_repo_path)
                
            except Exception as cleanup_error:
                logger.warning(f"Failed to clean up temporary repository: {str(cleanup_error)}")

# Redeploy GitHub site
@app.route('/api/github-deploy/<domain>', methods=['POST'])
@admin_required
def redeploy_github_site(domain):
    site = get_site_by_domain(domain)
    
    if not site or site.get('type') != 'github':
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
        
        # Update site in database
        now = datetime.datetime.now().isoformat()
        site_data = {
            'domain': domain,
            'type': 'zip',
            'updated_at': now
        }
        
        existing_site = get_site_by_domain(domain)
        if not existing_site:
            site_data['created_at'] = now
            
        upsert_site(domain, site_data)
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
        # Get sites from filesystem
        fs_sites = os.listdir(SITE_FOLDER)
        
        # Get sites from database
        db_sites = list(sites_collection.find({}, {'_id': 0, 'domain': 1}))
        db_site_domains = [site['domain'] for site in db_sites]
        
        return {
            'filesystem_sites': fs_sites,
            'database_sites': db_site_domains,
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

# Route to handle 404 errors with our custom template
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

# Added helper function to handle read-only files during deletion
def remove_readonly(func, path, excinfo):
    """
    Error handler for `shutil.rmtree` to handle read-only files.
    Make the file writable and try again.
    """
    logger.info(f"Trying to handle read-only file: {path}")
    os.chmod(path, stat.S_IWRITE)
    func(path)

# Helper function for Windows to force delete stubborn files
def force_delete_windows(path):
    """
    Use Windows API to force delete a file even if locked by another process.
    Returns True if successful, False otherwise.
    """
    try:
        logger.info(f"Attempting forced delete of: {path}")
        # Convert path to absolute for the Windows API
        abs_path = os.path.abspath(path)
        # Convert forward slashes to backslashes for Windows API
        path_for_win = abs_path.replace('/', '\\')
        # Use MoveFileExW with MOVEFILE_DELAY_UNTIL_REBOOT flag (4)
        result = windll.kernel32.MoveFileExW(path_for_win, None, c_uint32(4))
        if result == 0:  # If failed, get the error code
            error_code = windll.kernel32.GetLastError()
            logger.warning(f"Forced delete failed with error code: {error_code}")
            return False
        return True
    except Exception as e:
        logger.warning(f"Exception during forced delete: {str(e)}")
        return False

# Safe recursive deletion with retry mechanism
def safe_rmtree(path, max_retries=3, delay=1):
    """
    Safely remove a directory and all its contents with retry mechanism.
    
    Args:
        path: Directory path to remove
        max_retries: Maximum number of retry attempts
        delay: Delay between retries in seconds
    """
    if not os.path.exists(path):
        logger.info(f"Path doesn't exist, nothing to delete: {path}")
        return
        
    for attempt in range(max_retries):
        try:
            logger.info(f"Removing directory (attempt {attempt+1}): {path}")
            shutil.rmtree(path, onerror=remove_readonly)
            logger.info(f"Successfully removed directory: {path}")
            return
        except Exception as e:
            logger.warning(f"Failed to remove directory (attempt {attempt+1}): {path}, error: {str(e)}")
            
            # Try to remove problematic Git files individually
            if '.git' in path:
                try:
                    for root, dirs, files in os.walk(path):
                        for file in files:
                            if file.endswith('.idx') or file.endswith('.pack'):
                                file_path = os.path.join(root, file)
                                try:
                                    os.chmod(file_path, stat.S_IWRITE)
                                    os.unlink(file_path)
                                except:
                                    force_delete_windows(file_path)
                except Exception as inner_e:
                    logger.warning(f"Error handling Git files: {str(inner_e)}")
            
            # Wait before retrying
            if attempt < max_retries - 1:
                time.sleep(delay)
    
    # If we get here, all retries failed
    logger.error(f"Failed to remove directory after {max_retries} attempts: {path}")
    
    # Last resort: try to schedule deletion on reboot for Windows
    try:
        force_delete_windows(path)
    except Exception as e:
        logger.error(f"Failed to schedule deletion on reboot: {str(e)}")

if __name__ == '__main__':
    # Initialize database with indexes if needed
    sites_collection.create_index('domain', unique=True)
    activities_collection.create_index('date')
    
    # Make sure to run with debug=True to see detailed error messages
    app.run(debug=True, port=80, host='0.0.0.0')