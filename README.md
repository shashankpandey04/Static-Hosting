# Static Hosting Panel

This project is a static website hosting panel built with Flask, designed for easy management of hosted sites. The admin interface is styled using Tailwind CSS, providing a modern and responsive user experience.

## Project Structure

```
static-hosting-panel
├── app.py                  # Main application script
├── .env                    # Environment variables
├── static
│   ├── css
│   │   └── tailwind.css    # Compiled Tailwind CSS styles
│   └── js
│       └── admin.js        # JavaScript for admin panel interactions
├── templates
│   ├── admin
│   │   ├── dashboard.html   # Admin dashboard page
│   │   ├── login.html       # Admin login page
│   │   └── sites.html       # Manage hosted sites page
│   ├── base.html           # Base template for all pages
│   └── index.html          # Landing page of the hosting panel
├── uploads
│   └── .gitkeep            # Keeps uploads directory tracked by Git
├── user_sites
│   └── .gitkeep            # Keeps user_sites directory tracked by Git
├── requirements.txt        # Python dependencies
├── tailwind.config.js      # Tailwind CSS configuration
├── postcss.config.js       # PostCSS configuration
└── README.md               # Project documentation
```

## Setup Instructions

1. **Clone the repository:**
   ```
   git clone <repository-url>
   cd static-hosting-panel
   ```

2. **Create a virtual environment:**
   ```
   python -m venv venv
   source venv/bin/activate  # On Windows use `venv\Scripts\activate`
   ```

3. **Install dependencies:**
   ```
   pip install -r requirements.txt
   ```

4. **Set up environment variables:**
   Create a `.env` file in the root directory and add your admin token:
   ```
   ADMIN_TOKEN="your_admin_token"
   ```

5. **Run the application:**
   ```
   python app.py
   ```

6. **Access the admin panel:**
   Open your web browser and go to `http://127.0.0.1:5000`.

## Usage

- Use the admin panel to upload and manage static sites.
- The dashboard provides an overview of hosted sites and their statuses.
- Admin authentication is required to access the upload functionality.

## License

This project is licensed under the MIT License. See the LICENSE file for more details.