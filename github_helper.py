import os
import stat
import shutil
import logging
import requests
import tempfile
import zipfile
import time
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

def clone_github_repo_via_zip(repo_url, branch, output_path, skip_git=True):
    """
    Clone a GitHub repository using the ZIP download API instead of git.
    
    Args:
        repo_url: GitHub repository URL (https://github.com/username/repo)
        branch: Branch name to download
        output_path: Path where to extract the repository
        skip_git: Whether to skip .git directory when extracting
    
    Returns:
        Path to the cloned repository files
    
    Raises:
        Exception: If any error occurs during the process
    """
    # Parse the GitHub URL to get username and repository
    parsed_url = urlparse(repo_url)
    path_parts = parsed_url.path.strip('/').split('/')
    
    if len(path_parts) < 2:
        raise ValueError(f"Invalid GitHub URL: {repo_url}")
    
    username = path_parts[0]
    repository = path_parts[1]
    
    # Create a GitHub ZIP API URL
    zip_url = f"https://github.com/{username}/{repository}/archive/refs/heads/{branch}.zip"
    
    logger.info(f"Downloading repository as ZIP from {zip_url}")
    
    # Create a temporary directory
    with tempfile.TemporaryDirectory() as temp_dir:
        # Download the ZIP file
        zip_path = os.path.join(temp_dir, "repo.zip")
        
        try:
            response = requests.get(zip_url, stream=True, timeout=60)
            response.raise_for_status()
            
            with open(zip_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    
            # Extract the ZIP file
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                # Get the root folder name within the zip
                root_folder = zip_ref.namelist()[0].split('/')[0]
                
                # Extract all files
                for file_info in zip_ref.infolist():
                    filename = file_info.filename
                    # Skip .git directory if specified
                    if skip_git and '/.git/' in filename:
                        continue
                        
                    # Remove the root folder from extraction path
                    if filename.startswith(root_folder):
                        # Get path relative to root folder
                        relative_path = filename[len(root_folder)+1:]
                        if not relative_path:
                            continue  # Skip the root directory itself
                            
                        # Construct output path
                        target_path = os.path.join(output_path, relative_path)
                        
                        # Create directory if needed
                        if filename.endswith('/'):
                            os.makedirs(target_path, exist_ok=True)
                            continue
                        
                        # Extract file
                        os.makedirs(os.path.dirname(target_path), exist_ok=True)
                        with zip_ref.open(filename) as source, open(target_path, 'wb') as target:
                            shutil.copyfileobj(source, target)
            
            logger.info(f"Repository extracted successfully to {output_path}")
            return output_path
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error downloading repository: {str(e)}")
            raise Exception(f"Failed to download repository: {str(e)}")
        except zipfile.BadZipFile as e:
            logger.error(f"Error extracting ZIP file: {str(e)}")
            raise Exception(f"Failed to extract repository: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error during repository download: {str(e)}")
            raise
