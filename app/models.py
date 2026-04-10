from datetime import datetime, date, timedelta
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from . import db


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Plan(db.Model, TimestampMixin):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), unique=True, nullable=False)
    monthly_price = db.Column(db.Float, default=0)
    annual_price = db.Column(db.Float, default=0)
    branch_limit = db.Column(db.Integer, default=1)
    user_limit = db.Column(db.Integer, default=5)
    features_json = db.Column(db.Text, default="[]")
    status = db.Column(db.String(20), default="active")


class Tenant(db.Model, TimestampMixin):
    id = db.Column(db.Integer, primary_key=True)
    business_name = db.Column(db.String(120), nullable=False, unique=True)
    owner_name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(30), default="")
    email = db.Column(db.String(120), default="")
    address = db.Column(db.String(255), default="")
    plan_id = db.Column(db.Integer, db.ForeignKey("plan.id"))
    status = db.Column(db.String(20), default="trial")  # active, paused, frozen, expired, trial
    start_date = db.Column(db.Date, default=date.today)
    expiry_date = db.Column(db.Date, default=lambda: date.today() + timedelta(days=30))
    grace_days = db.Column(db.Integer, default=3)
    plan = db.relationship("Plan")

    def is_access_allowed(self):
        if self.status in {"paused", "frozen"}:
            return False
        if self.expiry_date and date.today() > (self.expiry_date + timedelta(days=self.grace_days)):
            self.status = "expired"
            db.session.commit()
            return False
        return True


class Branch(db.Model, TimestampMixin):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    code = db.Column(db.String(30), default="")
    phone = db.Column(db.String(30), default="")
    email = db.Column(db.String(120), default="")
    address = db.Column(db.String(255), default="")
    status = db.Column(db.String(20), default="active")
    tenant = db.relationship("Tenant", backref="branches")


class User(db.Model, UserMixin, TimestampMixin):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), nullable=True)
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"), nullable=True)
    full_name = db.Column(db.String(120), nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), default="")
    phone = db.Column(db.String(30), default="")
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), nullable=False, default="cashier")
    status = db.Column(db.String(20), default="active")
    must_change_password = db.Column(db.Boolean, default=False)
    last_login = db.Column(db.DateTime)
    tenant = db.relationship("Tenant", backref="users")
    branch = db.relationship("Branch", backref="users")

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str):
        return check_password_hash(self.password_hash, password)

    @property
    def is_super_admin(self):
        return self.role == "super_admin"

    @property
    def is_tenant_owner(self):
        return self.role == "tenant_owner"

    @property
    def is_branch_manager(self):
        return self.role == "branch_manager"

    @property
    def is_cashier(self):
        return self.role == "cashier"

    @property
    def is_stock_clerk(self):
        return self.role == "stock_clerk"

    def has_any_role(self, *roles):
        return self.role in roles


class Category(db.Model, TimestampMixin):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(255), default="")
    status = db.Column(db.String(20), default="active")
    tenant = db.relationship("Tenant", backref="categories")


class Product(db.Model, TimestampMixin):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=True)
    name = db.Column(db.String(120), nullable=False)
    barcode = db.Column(db.String(80), default="")
    sku = db.Column(db.String(80), default="")
    brand = db.Column(db.String(80), default="")
    unit = db.Column(db.String(30), default="each")
    cost_price = db.Column(db.Float, default=0)
    selling_price = db.Column(db.Float, default=0)
    tax_rate = db.Column(db.Float, default=0)
    reorder_level = db.Column(db.Float, default=0)
    image_path = db.Column(db.String(255), default="")
    status = db.Column(db.String(20), default="active")
    tenant = db.relationship("Tenant", backref="products")
    category = db.relationship("Category")


class Supplier(db.Model, TimestampMixin):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(30), default="")
    email = db.Column(db.String(120), default="")
    address = db.Column(db.String(255), default="")
    balance = db.Column(db.Float, default=0)
    status = db.Column(db.String(20), default="active")
    tenant = db.relationship("Tenant", backref="suppliers")


class Customer(db.Model, TimestampMixin):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(30), default="")
    email = db.Column(db.String(120), default="")
    address = db.Column(db.String(255), default="")
    balance = db.Column(db.Float, default=0)
    status = db.Column(db.String(20), default="active")
    tenant = db.relationship("Tenant", backref="customers")


class Inventory(db.Model, TimestampMixin):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), nullable=False)
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    quantity = db.Column(db.Float, default=0)
    tenant = db.relationship("Tenant")
    branch = db.relationship("Branch")
    product = db.relationship("Product")
    __table_args__ = (db.UniqueConstraint("tenant_id", "branch_id", "product_id", name="uniq_inventory"),)


class InventoryMovement(db.Model, TimestampMixin):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, nullable=False)
    branch_id = db.Column(db.Integer, nullable=False)
    product_id = db.Column(db.Integer, nullable=False)
    movement_type = db.Column(db.String(30), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    reference_type = db.Column(db.String(30), default="")
    reference_id = db.Column(db.Integer, nullable=True)
    note = db.Column(db.String(255), default="")
    created_by = db.Column(db.Integer, nullable=True)


class Purchase(db.Model, TimestampMixin):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, nullable=False)
    branch_id = db.Column(db.Integer, nullable=False)
    supplier_id = db.Column(db.Integer, nullable=True)
    invoice_number = db.Column(db.String(80), default="")
    subtotal = db.Column(db.Float, default=0)
    tax_total = db.Column(db.Float, default=0)
    grand_total = db.Column(db.Float, default=0)
    status = db.Column(db.String(20), default="received")
    note = db.Column(db.String(255), default="")
    created_by = db.Column(db.Integer, nullable=True)


class PurchaseItem(db.Model, TimestampMixin):
    id = db.Column(db.Integer, primary_key=True)
    purchase_id = db.Column(db.Integer, db.ForeignKey("purchase.id"), nullable=False)
    product_id = db.Column(db.Integer, nullable=False)
    quantity = db.Column(db.Float, default=0)
    cost_price = db.Column(db.Float, default=0)
    line_total = db.Column(db.Float, default=0)
    purchase = db.relationship("Purchase", backref="items")


class Sale(db.Model, TimestampMixin):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, nullable=False)
    branch_id = db.Column(db.Integer, nullable=False)
    cashier_id = db.Column(db.Integer, nullable=False)
    customer_id = db.Column(db.Integer, nullable=True)
    sale_number = db.Column(db.String(50), unique=True, nullable=False)
    status = db.Column(db.String(20), default="completed")
    subtotal = db.Column(db.Float, default=0)
    discount_total = db.Column(db.Float, default=0)
    tax_total = db.Column(db.Float, default=0)
    grand_total = db.Column(db.Float, default=0)
    payment_status = db.Column(db.String(20), default="paid")
    note = db.Column(db.String(255), default="")
    is_printed = db.Column(db.Boolean, default=False)
    printed_at = db.Column(db.DateTime, nullable=True)
    printed_by = db.Column(db.Integer, nullable=True)
    reprint_count = db.Column(db.Integer, default=0)


class SaleItem(db.Model, TimestampMixin):
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sale.id"), nullable=False)
    product_id = db.Column(db.Integer, nullable=False)
    product_name_snapshot = db.Column(db.String(120), nullable=False)
    barcode_snapshot = db.Column(db.String(80), default="")
    quantity = db.Column(db.Float, default=1)
    unit_price = db.Column(db.Float, default=0)
    discount_amount = db.Column(db.Float, default=0)
    tax_amount = db.Column(db.Float, default=0)
    line_total = db.Column(db.Float, default=0)
    sale = db.relationship("Sale", backref="items")


class SalePayment(db.Model, TimestampMixin):
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sale.id"), nullable=False)
    payment_method = db.Column(db.String(30), nullable=False)
    amount = db.Column(db.Float, default=0)
    reference = db.Column(db.String(80), default="")
    sale = db.relationship("Sale", backref="payments")


class Return(db.Model, TimestampMixin):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, nullable=False)
    branch_id = db.Column(db.Integer, nullable=False)
    sale_id = db.Column(db.Integer, nullable=False)
    processed_by = db.Column(db.Integer, nullable=False)
    refund_method = db.Column(db.String(30), default="cash")
    reason = db.Column(db.String(255), default="")
    refund_total = db.Column(db.Float, default=0)


class ReturnItem(db.Model, TimestampMixin):
    id = db.Column(db.Integer, primary_key=True)
    return_id = db.Column(db.Integer, db.ForeignKey("return.id"), nullable=False)
    sale_item_id = db.Column(db.Integer, nullable=False)
    quantity = db.Column(db.Float, default=0)
    refund_amount = db.Column(db.Float, default=0)
    return_ref = db.relationship("Return", backref="items")


class Expense(db.Model, TimestampMixin):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, nullable=False)
    branch_id = db.Column(db.Integer, nullable=False)
    category = db.Column(db.String(80), nullable=False)
    amount = db.Column(db.Float, default=0)
    payment_method = db.Column(db.String(30), default="cash")
    note = db.Column(db.String(255), default="")
    expense_date = db.Column(db.Date, default=date.today)
    created_by = db.Column(db.Integer, nullable=True)


class Shift(db.Model, TimestampMixin):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, nullable=False)
    branch_id = db.Column(db.Integer, nullable=False)
    cashier_id = db.Column(db.Integer, nullable=False)
    opened_at = db.Column(db.DateTime, default=datetime.utcnow)
    closed_at = db.Column(db.DateTime, nullable=True)
    opening_float = db.Column(db.Float, default=0)
    closing_total = db.Column(db.Float, default=0)
    status = db.Column(db.String(20), default="open")


class Cashup(db.Model, TimestampMixin):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, nullable=False)
    branch_id = db.Column(db.Integer, nullable=False)
    cashier_id = db.Column(db.Integer, nullable=False)
    shift_date = db.Column(db.Date, default=date.today)
    expected_cash = db.Column(db.Float, default=0)
    actual_cash = db.Column(db.Float, default=0)
    counted_cash = db.Column(db.Float, default=0)
    variance = db.Column(db.Float, default=0)
    note = db.Column(db.String(255), default="")


class StockTransfer(db.Model, TimestampMixin):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, nullable=False)
    from_branch_id = db.Column(db.Integer, nullable=False)
    to_branch_id = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default="completed")
    note = db.Column(db.String(255), default="")
    created_by = db.Column(db.Integer, nullable=True)


class StockTransferItem(db.Model, TimestampMixin):
    id = db.Column(db.Integer, primary_key=True)
    transfer_id = db.Column(db.Integer, db.ForeignKey("stock_transfer.id"), nullable=False)
    product_id = db.Column(db.Integer, nullable=False)
    quantity = db.Column(db.Float, default=0)
    transfer = db.relationship("StockTransfer", backref="items")


class TenantSetting(db.Model, TimestampMixin):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, nullable=False, unique=True)
    currency = db.Column(db.String(10), default="USD")
    timezone = db.Column(db.String(50), default="Africa/Harare")
    receipt_footer = db.Column(db.String(255), default="Powered by Technoplus | +263 773 464 209 | handiwechitatenda@gmail.com")
    theme_color = db.Column(db.String(20), default="#e3a72f")
    logo_path = db.Column(db.String(255), default="")
    support_phone = db.Column(db.String(30), default="+263 773 464 209")
    support_email = db.Column(db.String(120), default="handiwechitatenda@gmail.com")
    printer_name = db.Column(db.String(120), default="Browser Print")
    paper_width_mm = db.Column(db.Integer, default=80)
    auto_print_receipt = db.Column(db.Boolean, default=False)


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, nullable=True)
    branch_id = db.Column(db.Integer, nullable=True)
    user_id = db.Column(db.Integer, nullable=True)
    action_type = db.Column(db.String(60), nullable=False)
    entity_type = db.Column(db.String(60), nullable=False)
    entity_id = db.Column(db.Integer, nullable=True)
    description = db.Column(db.String(255), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
