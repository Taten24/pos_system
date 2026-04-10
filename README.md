# Technoplus POS Production V3

A multi-tenant Flask POS starter with:
- super admin / owner controls
- tenant + branch isolation
- freeze / pause / activate tenants and branches
- password hashing + forced password change
- users, categories, products, suppliers, customers
- inventory + adjustments
- receive stock with multi-line items
- multi-item cart POS sales
- receipts with branding and support contact
- returns
- expenses
- stock transfers
- cash-up
- reports
- audit logs
- themed UI

## Default admin login
- Username: `admin`
- Password: `admin123`

Change the password immediately after first login.

## Run
```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

Open:
- http://127.0.0.1:5000

## Notes
- Database: SQLite in `instance/technoplus_pos.db`
- Built as a production-style starter. You can extend with:
  - PostgreSQL
  - barcode printing
  - PDF exports
  - API endpoints
  - payment integration
  - branch-specific pricing


V4 updates:
- role-based sidebar and page protections
- user access editing
- receipt navigation buttons
- printer settings (browser print, paper width, auto-print)
