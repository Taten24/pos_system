from datetime import date, timedelta
from sqlalchemy import func
from . import db
from .models import (
    Plan, User, Tenant, Branch, TenantSetting, AuditLog,
    Inventory, InventoryMovement, Sale, Expense, Product
)


def seed_defaults():
    if not Plan.query.first():
        db.session.add_all([
            Plan(name="Starter", monthly_price=10, annual_price=100, branch_limit=1, user_limit=5,
                 features_json='["sales","inventory","reports"]'),
            Plan(name="Standard", monthly_price=20, annual_price=200, branch_limit=3, user_limit=15,
                 features_json='["sales","inventory","reports","expenses","suppliers"]'),
            Plan(name="Premium", monthly_price=35, annual_price=350, branch_limit=10, user_limit=50,
                 features_json='["all"]'),
        ])
        db.session.commit()

    if not User.query.filter_by(role="super_admin").first():
        admin = User(full_name="System Owner", username="admin", email="handiwechitatenda@gmail.com",
                     role="super_admin", status="active")
        admin.set_password("admin123")
        db.session.add(admin)
        db.session.commit()
        audit("seed", "user", admin.id, "Created default super admin", user_id=admin.id)


def audit(action_type, entity_type, entity_id=None, description="", user_id=None, tenant_id=None, branch_id=None):
    db.session.add(AuditLog(
        action_type=action_type,
        entity_type=entity_type,
        entity_id=entity_id,
        description=description,
        user_id=user_id,
        tenant_id=tenant_id,
        branch_id=branch_id
    ))
    db.session.commit()


def get_or_create_inventory(tenant_id, branch_id, product_id):
    item = Inventory.query.filter_by(tenant_id=tenant_id, branch_id=branch_id, product_id=product_id).first()
    if not item:
        item = Inventory(tenant_id=tenant_id, branch_id=branch_id, product_id=product_id, quantity=0)
        db.session.add(item)
        db.session.commit()
    return item


def move_inventory(tenant_id, branch_id, product_id, qty, movement_type, reference_type="", reference_id=None, note="", user_id=None):
    item = get_or_create_inventory(tenant_id, branch_id, product_id)
    item.quantity += qty
    db.session.add(InventoryMovement(
        tenant_id=tenant_id,
        branch_id=branch_id,
        product_id=product_id,
        movement_type=movement_type,
        quantity=qty,
        reference_type=reference_type,
        reference_id=reference_id,
        note=note,
        created_by=user_id
    ))
    db.session.commit()
    return item


def tenant_dashboard_metrics(tenant_id):
    today = date.today()
    today_sales = db.session.query(func.coalesce(func.sum(Sale.grand_total), 0)).filter(
        Sale.tenant_id == tenant_id, func.date(Sale.created_at) == today
    ).scalar() or 0
    month_sales = db.session.query(func.coalesce(func.sum(Sale.grand_total), 0)).filter(
        Sale.tenant_id == tenant_id,
        func.strftime('%Y-%m', Sale.created_at) == today.strftime('%Y-%m')
    ).scalar() or 0
    month_expenses = db.session.query(func.coalesce(func.sum(Expense.amount), 0)).filter(
        Expense.tenant_id == tenant_id,
        func.strftime('%Y-%m', Expense.created_at) == today.strftime('%Y-%m')
    ).scalar() or 0

    top_products = db.session.query(
        Product.name, func.coalesce(func.sum(InventoryMovement.quantity * -1), 0).label('sold')
    ).join(InventoryMovement, InventoryMovement.product_id == Product.id).filter(
        Product.tenant_id == tenant_id, InventoryMovement.movement_type == 'sale'
    ).group_by(Product.name).order_by(func.sum(InventoryMovement.quantity * -1).desc()).limit(5).all()

    low_stock = db.session.query(Product.name, func.coalesce(func.sum(Inventory.quantity), 0).label('qty'), Product.reorder_level).join(
        Inventory, Inventory.product_id == Product.id
    ).filter(Product.tenant_id == tenant_id).group_by(Product.id).having(
        func.coalesce(func.sum(Inventory.quantity), 0) <= Product.reorder_level
    ).all()

    return {
        "today_sales": today_sales,
        "month_sales": month_sales,
        "month_expenses": month_expenses,
        "gross_estimate": month_sales - month_expenses,
        "top_products": top_products,
        "low_stock": low_stock,
    }
