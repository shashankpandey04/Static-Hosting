// filepath: static-hosting-panel/static/js/admin.js
document.addEventListener('DOMContentLoaded', function() {
    const uploadForm = document.getElementById('uploadForm');
    const domainInput = document.getElementById('domain');
    const tokenInput = document.getElementById('token');
    const zipInput = document.getElementById('zipfile');

    uploadForm.addEventListener('submit', function(event) {
        if (!domainInput.value || !tokenInput.value || !zipInput.files.length) {
            event.preventDefault();
            alert('Please fill in all fields and select a zip file.');
        }
    });

    // Example of dynamic interaction: show a success message after upload
    const successMessage = document.getElementById('successMessage');
    if (successMessage) {
        setTimeout(() => {
            successMessage.style.display = 'none';
        }, 3000);
    }
});