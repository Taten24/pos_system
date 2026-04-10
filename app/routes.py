
from datetime import date, datetime, timedelta
from collections import defaultdict
from flask import render_template, request, redirect, url_for, flash, session, abort
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy import func, or_
from . import db
from .models import (
    User, Tenant, Branch, Plan, Category, Product, Supplier, Customer,
    Inventory, Purchase, PurchaseItem, Sale, SaleItem, SalePayment, Expense, Cashup,
    Shift, StockTransfer, StockTransferItem, Return, ReturnItem, TenantSetting, AuditLog
)
from .utils import audit, get_or_create_inventory, move_inventory, tenant_dashboard_metrics

SUPPORT_PHONE = "+263 773 464 209"
SUPPORT_EMAIL = "handiwechitatenda@gmail.com"


def register_routes(app):
    @app.context_processor
    def inject_globals():
        return {
            "support_phone": SUPPORT_PHONE,
            "support_email": SUPPORT_EMAIL,
            "today": date.today(),
        }

    def require_super_admin():
        if not current_user.is_authenticated or not current_user.is_super_admin:
            abort(403)

    def require_tenant_access(tenant_id=None):
        if not current_user.is_authenticated:
            abort(403)
        if current_user.is_super_admin:
            return
        tenant = current_user.tenant
        if not tenant or not tenant.is_access_allowed():
            abort(403)
        if tenant_id and current_user.tenant_id != tenant_id:
            abort(403)

    def require_branch_active(branch_id):
        branch = Branch.query.get_or_404(branch_id)
        if branch.status != "active":
            flash("This branch is frozen or inactive.", "danger")
            abort(403)
        if branch.tenant and not branch.tenant.is_access_allowed():
            flash("The tenant account is paused or expired.", "danger")
            abort(403)
        return branch

    def require_branch_access(branch_id):
        if not current_user.is_authenticated:
            abort(403)
        if current_user.is_super_admin:
            return Branch.query.get_or_404(branch_id)
        branch = Branch.query.get_or_404(branch_id)
        if current_user.tenant_id != branch.tenant_id:
            flash("You do not have access to that branch.", "danger")
            abort(403)
        if current_user.role in ["branch_manager", "cashier", "stock_clerk"] and current_user.branch_id != branch_id:
            flash("You can only access your assigned branch.", "danger")
            abort(403)
        return branch

    def require_roles(*roles):
        if not current_user.is_authenticated:
            abort(403)
        if current_user.is_super_admin:
            return
        if roles and not current_user.has_any_role(*roles):
            flash("You do not have permission to access this section.", "danger")
            abort(403)

    def can_manage_returns():
        return current_user.is_super_admin or current_user.has_any_role("tenant_owner", "branch_manager")

    def calculate_expected_cash(branch_id, for_date=None, cashier_id=None):
        for_date = for_date or date.today()
        q = db.session.query(func.coalesce(func.sum(SalePayment.amount), 0.0)).join(
            Sale, Sale.id == SalePayment.sale_id
        ).filter(
            Sale.tenant_id == current_user.tenant_id,
            Sale.branch_id == branch_id,
            SalePayment.payment_method == "cash",
            Sale.status == "completed",
            func.date(Sale.created_at) == for_date,
        )
        if cashier_id:
            q = q.filter(Sale.cashier_id == cashier_id)
        return float(q.scalar() or 0.0)

    def cart_key():
        return f"cart_{current_user.id}"

    def get_cart():
        session.setdefault(cart_key(), {})
        return session[cart_key()]

    def save_cart(cart):
        session[cart_key()] = cart
        session.modified = True

    @app.route("/")
    def landing():
        plans = Plan.query.order_by(Plan.monthly_price).all()
        return render_template("landing.html", plans=plans)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "").strip()
            user = User.query.filter_by(username=username).first()
            if not user or not user.check_password(password):
                flash("Invalid username or password.", "danger")
                return redirect(url_for("login"))
            if user.status != "active":
                flash("Your account is inactive or frozen.", "danger")
                return redirect(url_for("login"))
            if user.tenant and not user.tenant.is_access_allowed():
                flash("Tenant account paused, frozen, or expired. Contact administrator.", "danger")
                return redirect(url_for("login"))
            if user.branch and user.branch.status != "active":
                flash("Your branch is frozen or inactive.", "danger")
                return redirect(url_for("login"))
            user.last_login = datetime.utcnow()
            db.session.commit()
            login_user(user)
            if user.must_change_password:
                flash("Please change your password first.", "warning")
                return redirect(url_for("change_password"))
            if user.is_super_admin:
                return redirect(url_for("admin_dashboard"))
            return redirect(url_for("tenant_dashboard"))
        return render_template("login.html")

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        return redirect(url_for("login"))

    @app.route("/change-password", methods=["GET", "POST"])
    @login_required
    def change_password():
        if request.method == "POST":
            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirm_password = request.form.get("confirm_password", "")
            if not current_user.check_password(current_password):
                flash("Current password is incorrect.", "danger")
            elif len(new_password) < 6:
                flash("New password must be at least 6 characters.", "danger")
            elif new_password != confirm_password:
                flash("Passwords do not match.", "danger")
            else:
                current_user.set_password(new_password)
                current_user.must_change_password = False
                db.session.commit()
                flash("Password changed successfully.", "success")
                return redirect(url_for("admin_dashboard" if current_user.is_super_admin else "tenant_dashboard"))
        return render_template("change_password.html")

    # ---------------------- SUPER ADMIN ----------------------
    @app.route("/admin")
    @login_required
    def admin_dashboard():
        require_super_admin()
        tenants = Tenant.query.order_by(Tenant.business_name).all()
        recent_logs = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(10).all()
        counts = {
            "tenants": Tenant.query.count(),
            "active": Tenant.query.filter_by(status="active").count(),
            "frozen": Tenant.query.filter(Tenant.status.in_(["frozen", "paused"])).count(),
            "expired": Tenant.query.filter_by(status="expired").count(),
            "branches": Branch.query.count(),
        }
        return render_template("admin_dashboard.html", tenants=tenants, counts=counts, recent_logs=recent_logs)

    @app.route("/admin/tenants")
    @login_required
    def tenants():
        require_super_admin()
        tenants = Tenant.query.order_by(Tenant.created_at.desc()).all()
        return render_template("tenants.html", tenants=tenants)

    @app.route("/admin/tenant/new", methods=["GET", "POST"])
    @login_required
    def new_tenant():
        require_super_admin()
        plans = Plan.query.all()
        if request.method == "POST":
            business_name = request.form.get("business_name", "").strip()
            owner_name = request.form.get("owner_name", "").strip()
            phone = request.form.get("phone", "").strip()
            email = request.form.get("email", "").strip()
            address = request.form.get("address", "").strip()
            branch_name = request.form.get("branch_name", "Main Branch").strip()
            owner_username = request.form.get("owner_username", "").strip()
            owner_password = request.form.get("owner_password", "welcome123").strip()
            plan_id = request.form.get("plan_id", type=int)

            if not business_name or not owner_name or not owner_username:
                flash("Business name, owner name, and owner username are required.", "danger")
                return redirect(url_for("new_tenant"))
            if User.query.filter_by(username=owner_username).first():
                flash("Username already exists.", "danger")
                return redirect(url_for("new_tenant"))

            tenant = Tenant(
                business_name=business_name, owner_name=owner_name, phone=phone, email=email, address=address,
                plan_id=plan_id, status="active", start_date=date.today(), expiry_date=date.today() + timedelta(days=30)
            )
            db.session.add(tenant)
            db.session.flush()
            branch = Branch(tenant_id=tenant.id, name=branch_name, code="MAIN", phone=phone, email=email, address=address, status="active")
            db.session.add(branch)
            db.session.flush()
            owner = User(
                tenant_id=tenant.id, branch_id=branch.id, full_name=owner_name, username=owner_username, email=email, phone=phone,
                role="tenant_owner", status="active", must_change_password=True
            )
            owner.set_password(owner_password)
            db.session.add(owner)
            db.session.add(TenantSetting(tenant_id=tenant.id))
            db.session.commit()
            audit("create", "tenant", tenant.id, f"Created tenant {tenant.business_name}", user_id=current_user.id)
            flash("Tenant created successfully.", "success")
            return redirect(url_for("tenants"))
        return render_template("tenant_form.html", plans=plans)

    @app.route("/admin/tenant/<int:tenant_id>/toggle-status/<status>")
    @login_required
    def toggle_tenant_status(tenant_id, status):
        require_super_admin()
        tenant = Tenant.query.get_or_404(tenant_id)
        if status not in {"active", "paused", "frozen", "expired"}:
            flash("Invalid status.", "danger")
        else:
            tenant.status = status
            db.session.commit()
            audit("status", "tenant", tenant.id, f"Set tenant {tenant.business_name} to {status}", user_id=current_user.id)
            flash("Tenant status updated.", "success")
        return redirect(request.referrer or url_for("tenants"))

    @app.route("/admin/tenant/<int:tenant_id>/branches", methods=["GET", "POST"])
    @login_required
    def tenant_branches(tenant_id):
        require_super_admin()
        tenant = Tenant.query.get_or_404(tenant_id)
        if request.method == "POST":
            branch = Branch(
                tenant_id=tenant.id,
                name=request.form.get("name", "").strip(),
                code=request.form.get("code", "").strip(),
                phone=request.form.get("phone", "").strip(),
                email=request.form.get("email", "").strip(),
                address=request.form.get("address", "").strip(),
                status="active"
            )
            db.session.add(branch)
            db.session.commit()
            audit("create", "branch", branch.id, f"Added branch {branch.name} to {tenant.business_name}", user_id=current_user.id, tenant_id=tenant.id)
            flash("Branch added.", "success")
            return redirect(url_for("tenant_branches", tenant_id=tenant.id))
        return render_template("branches.html", tenant=tenant)

    @app.route("/admin/branch/<int:branch_id>/toggle/<status>")
    @login_required
    def toggle_branch_status(branch_id, status):
        require_super_admin()
        branch = Branch.query.get_or_404(branch_id)
        if status not in {"active", "paused", "frozen"}:
            flash("Invalid status.", "danger")
        else:
            branch.status = status
            db.session.commit()
            audit("status", "branch", branch.id, f"Set branch {branch.name} to {status}", user_id=current_user.id, tenant_id=branch.tenant_id, branch_id=branch.id)
            flash("Branch status updated.", "success")
        return redirect(request.referrer or url_for("tenant_branches", tenant_id=branch.tenant_id))

    @app.route("/admin/audit")
    @login_required
    def audit_logs():
        require_super_admin()
        logs = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(200).all()
        return render_template("audit_logs.html", logs=logs)

    # ---------------------- TENANT / STORE ----------------------
    @app.route("/dashboard")
    @login_required
    def tenant_dashboard():
        if current_user.is_super_admin:
            return redirect(url_for("admin_dashboard"))
        require_tenant_access(current_user.tenant_id)
        metrics = tenant_dashboard_metrics(current_user.tenant_id)
        setting = TenantSetting.query.filter_by(tenant_id=current_user.tenant_id).first()
        return render_template("tenant_dashboard.html", metrics=metrics, setting=setting)

    @app.route("/users", methods=["GET", "POST"])
    @login_required
    def users():
        require_tenant_access(current_user.tenant_id)
        require_roles("tenant_owner", "branch_manager")
        tenant = current_user.tenant
        if request.method == "POST":
            action = request.form.get("action", "create")
            if action == "create":
                role = request.form.get("role", "cashier")
                if current_user.role == "branch_manager" and role in {"tenant_owner", "branch_manager"}:
                    flash("Branch managers cannot create owner or manager accounts.", "danger")
                    return redirect(url_for("users"))
                branch_id = request.form.get("branch_id", type=int)
                if current_user.role == "branch_manager":
                    branch_id = current_user.branch_id
                user = User(
                    tenant_id=tenant.id,
                    branch_id=branch_id,
                    full_name=request.form.get("full_name", "").strip(),
                    username=request.form.get("username", "").strip(),
                    email=request.form.get("email", "").strip(),
                    phone=request.form.get("phone", "").strip(),
                    role=role,
                    status="active",
                    must_change_password=True,
                )
                user.set_password(request.form.get("password", "welcome123"))
                db.session.add(user)
                db.session.commit()
                audit("create", "user", user.id, f"Added user {user.username}", user_id=current_user.id, tenant_id=tenant.id, branch_id=user.branch_id)
                flash("User added successfully.", "success")
            elif action == "update":
                user_id = request.form.get("user_id", type=int)
                user = User.query.get_or_404(user_id)
                require_tenant_access(user.tenant_id)
                if current_user.role == "branch_manager" and (user.role in {"tenant_owner", "branch_manager"} or user.branch_id != current_user.branch_id):
                    flash("You cannot edit that user.", "danger")
                    return redirect(url_for("users"))
                user.full_name = request.form.get("full_name", user.full_name).strip()
                user.email = request.form.get("email", user.email).strip()
                user.phone = request.form.get("phone", user.phone).strip()
                new_role = request.form.get("role", user.role)
                if not (current_user.role == "branch_manager" and new_role in {"tenant_owner", "branch_manager"}):
                    user.role = new_role
                if current_user.role != "branch_manager":
                    user.branch_id = request.form.get("branch_id", type=int)
                user.status = request.form.get("status", user.status)
                if request.form.get("reset_password"):
                    user.set_password(request.form.get("reset_password"))
                    user.must_change_password = True
                db.session.commit()
                audit("update", "user", user.id, f"Updated user {user.username}", user_id=current_user.id, tenant_id=tenant.id, branch_id=user.branch_id)
                flash("User updated successfully.", "success")
            return redirect(url_for("users"))
        if current_user.role == "branch_manager":
            tenant_users = User.query.filter_by(tenant_id=tenant.id, branch_id=current_user.branch_id).order_by(User.role, User.full_name).all()
            branches = [current_user.branch] if current_user.branch else []
        else:
            tenant_users = User.query.filter_by(tenant_id=tenant.id).order_by(User.role, User.full_name).all()
            branches = Branch.query.filter_by(tenant_id=tenant.id).all()
        return render_template("users.html", tenant_users=tenant_users, branches=branches)

    @app.route("/categories", methods=["GET", "POST"])
    @login_required
    def categories():
        require_tenant_access(current_user.tenant_id)
        require_roles("tenant_owner", "branch_manager", "cashier")
        if request.method == "POST":
            cat = Category(
                tenant_id=current_user.tenant_id,
                name=request.form.get("name", "").strip(),
                description=request.form.get("description", "").strip(),
                status="active"
            )
            db.session.add(cat)
            db.session.commit()
            audit("create", "category", cat.id, f"Added category {cat.name}", user_id=current_user.id, tenant_id=current_user.tenant_id)
            flash("Category added.", "success")
            return redirect(url_for("categories"))
        cats = Category.query.filter_by(tenant_id=current_user.tenant_id).order_by(Category.name).all()
        return render_template("categories.html", categories=cats)

    @app.route("/products", methods=["GET", "POST"])
    @login_required
    def products():
        require_tenant_access(current_user.tenant_id)
        require_roles("tenant_owner", "branch_manager", "stock_clerk", "cashier")
        categories = Category.query.filter_by(tenant_id=current_user.tenant_id).order_by(Category.name).all()
        can_manage_products = current_user.is_super_admin or current_user.has_any_role("tenant_owner", "branch_manager")
        if request.method == "POST":
            action = request.form.get("action", "create")
            if action in {"create", "update", "toggle_status"} and not can_manage_products:
                flash("You do not have permission to manage products.", "danger")
                return redirect(url_for("products"))

            if action == "create":
                product = Product(
                    tenant_id=current_user.tenant_id,
                    category_id=request.form.get("category_id", type=int),
                    name=request.form.get("name", "").strip(),
                    barcode=request.form.get("barcode", "").strip(),
                    sku=request.form.get("sku", "").strip(),
                    brand=request.form.get("brand", "").strip(),
                    unit=request.form.get("unit", "each").strip(),
                    cost_price=request.form.get("cost_price", type=float) or 0,
                    selling_price=request.form.get("selling_price", type=float) or 0,
                    tax_rate=request.form.get("tax_rate", type=float) or 0,
                    reorder_level=request.form.get("reorder_level", type=float) or 0,
                    status="active"
                )
                db.session.add(product)
                db.session.commit()
                for branch in Branch.query.filter_by(tenant_id=current_user.tenant_id).all():
                    get_or_create_inventory(current_user.tenant_id, branch.id, product.id)
                audit("create", "product", product.id, f"Added product {product.name}", user_id=current_user.id, tenant_id=current_user.tenant_id)
                flash("Product added.", "success")
                return redirect(url_for("products"))

            product_id = request.form.get("product_id", type=int)
            product = Product.query.filter_by(id=product_id, tenant_id=current_user.tenant_id).first_or_404()
            if action == "update":
                product.category_id = request.form.get("category_id", type=int)
                product.name = request.form.get("name", product.name).strip()
                product.barcode = request.form.get("barcode", product.barcode).strip()
                product.sku = request.form.get("sku", product.sku).strip()
                product.brand = request.form.get("brand", product.brand).strip()
                product.unit = request.form.get("unit", product.unit).strip() or "each"
                product.cost_price = request.form.get("cost_price", type=float) or 0
                product.selling_price = request.form.get("selling_price", type=float) or 0
                product.tax_rate = request.form.get("tax_rate", type=float) or 0
                product.reorder_level = request.form.get("reorder_level", type=float) or 0
                db.session.commit()
                audit("update", "product", product.id, f"Updated product {product.name}", user_id=current_user.id, tenant_id=current_user.tenant_id)
                flash("Product updated.", "success")
                return redirect(url_for("products", q=request.args.get("q", ""), status=request.args.get("status", "active")))
            elif action == "toggle_status":
                product.status = "inactive" if product.status == "active" else "active"
                db.session.commit()
                audit("toggle_status", "product", product.id, f"Set product {product.name} to {product.status}", user_id=current_user.id, tenant_id=current_user.tenant_id)
                flash(f"Product marked {product.status}.", "warning" if product.status == "inactive" else "success")
                return redirect(url_for("products", q=request.args.get("q", ""), status=request.args.get("status", "all")))

        q = (request.args.get("q") or "").strip()
        status_filter = (request.args.get("status") or "active").strip()
        products_query = Product.query.filter_by(tenant_id=current_user.tenant_id)
        if q:
            like = f"%{q}%"
            products_query = products_query.filter(or_(
                Product.name.ilike(like),
                Product.barcode.ilike(like),
                Product.sku.ilike(like),
                Product.brand.ilike(like)
            ))
        if status_filter == "active":
            products_query = products_query.filter_by(status="active")
        elif status_filter == "inactive":
            products_query = products_query.filter_by(status="inactive")
        all_products = products_query.order_by(Product.name).all()
        return render_template("products.html", products=all_products, categories=categories, q=q, status_filter=status_filter, can_manage_products=can_manage_products)

    @app.route("/suppliers", methods=["GET", "POST"])
    @login_required
    def suppliers():
        require_tenant_access(current_user.tenant_id)
        if request.method == "POST":
            supplier = Supplier(
                tenant_id=current_user.tenant_id,
                name=request.form.get("name", "").strip(),
                phone=request.form.get("phone", "").strip(),
                email=request.form.get("email", "").strip(),
                address=request.form.get("address", "").strip(),
                status="active"
            )
            db.session.add(supplier)
            db.session.commit()
            audit("create", "supplier", supplier.id, f"Added supplier {supplier.name}", user_id=current_user.id, tenant_id=current_user.tenant_id)
            flash("Supplier added.", "success")
            return redirect(url_for("suppliers"))
        all_suppliers = Supplier.query.filter_by(tenant_id=current_user.tenant_id).order_by(Supplier.name).all()
        return render_template("suppliers.html", suppliers=all_suppliers)

    @app.route("/customers", methods=["GET", "POST"])
    @login_required
    def customers():
        require_tenant_access(current_user.tenant_id)
        if request.method == "POST":
            customer = Customer(
                tenant_id=current_user.tenant_id,
                name=request.form.get("name", "").strip(),
                phone=request.form.get("phone", "").strip(),
                email=request.form.get("email", "").strip(),
                address=request.form.get("address", "").strip(),
                status="active"
            )
            db.session.add(customer)
            db.session.commit()
            audit("create", "customer", customer.id, f"Added customer {customer.name}", user_id=current_user.id, tenant_id=current_user.tenant_id)
            flash("Customer added.", "success")
            return redirect(url_for("customers"))
        all_customers = Customer.query.filter_by(tenant_id=current_user.tenant_id).order_by(Customer.name).all()
        return render_template("customers.html", customers=all_customers)

    @app.route("/inventory", methods=["GET", "POST"])
    @login_required
    def inventory():
        require_tenant_access(current_user.tenant_id)
        require_roles("tenant_owner", "branch_manager", "stock_clerk")
        branches_query = Branch.query.filter_by(tenant_id=current_user.tenant_id)
        if current_user.role in ["branch_manager", "stock_clerk"] and current_user.branch_id:
            branches_query = branches_query.filter_by(id=current_user.branch_id)
        branches = branches_query.order_by(Branch.name).all()
        products = Product.query.filter_by(tenant_id=current_user.tenant_id).order_by(Product.name).all()
        selected_branch_id = request.values.get("branch_id", type=int)
        if request.method == "POST":
            branch_id = request.form.get("branch_id", type=int)
            product_id = request.form.get("product_id", type=int)
            qty = request.form.get("quantity", type=float) or 0
            reason = request.form.get("reason", "").strip()
            direction = request.form.get("direction", "in")
            require_branch_access(branch_id)
            move_inventory(
                current_user.tenant_id, branch_id, product_id, qty if direction == "in" else -qty,
                "adjustment_in" if direction == "in" else "adjustment_out",
                note=reason, user_id=current_user.id
            )
            audit("adjust", "inventory", product_id, f"Adjusted stock by {qty} ({direction})", user_id=current_user.id, tenant_id=current_user.tenant_id, branch_id=branch_id)
            flash("Inventory adjusted.", "success")
            return redirect(url_for("inventory", branch_id=branch_id))
        inventory_query = db.session.query(Inventory, Product, Branch).join(Product, Inventory.product_id == Product.id).join(
            Branch, Inventory.branch_id == Branch.id
        ).filter(Inventory.tenant_id == current_user.tenant_id)
        if current_user.role in ["branch_manager", "stock_clerk"] and current_user.branch_id:
            inventory_query = inventory_query.filter(Inventory.branch_id == current_user.branch_id)
            selected_branch_id = current_user.branch_id
        elif selected_branch_id:
            inventory_query = inventory_query.filter(Inventory.branch_id == selected_branch_id)
        inventory_rows = inventory_query.order_by(Branch.name, Product.name).all()
        return render_template("inventory.html", inventory_rows=inventory_rows, branches=branches, products=products, selected_branch_id=selected_branch_id)

    @app.route("/inventory/stock-sheet")
    @login_required
    def print_stock_sheet():
        require_tenant_access(current_user.tenant_id)
        require_roles("tenant_owner", "branch_manager", "stock_clerk")
        selected_branch_id = request.args.get("branch_id", type=int)
        inventory_query = db.session.query(Inventory, Product, Branch).join(Product, Inventory.product_id == Product.id).join(
            Branch, Inventory.branch_id == Branch.id
        ).filter(Inventory.tenant_id == current_user.tenant_id)
        if current_user.role in ["branch_manager", "stock_clerk"] and current_user.branch_id:
            inventory_query = inventory_query.filter(Inventory.branch_id == current_user.branch_id)
            selected_branch_id = current_user.branch_id
        elif selected_branch_id:
            require_branch_access(selected_branch_id)
            inventory_query = inventory_query.filter(Inventory.branch_id == selected_branch_id)
        rows = inventory_query.order_by(Branch.name, Product.name).all()
        grouped_rows = defaultdict(list)
        for inv, product, branch in rows:
            grouped_rows[branch.name].append((inv, product, branch))
        return render_template(
            "stock_sheet.html",
            grouped_rows=grouped_rows,
            generated_at=datetime.utcnow(),
            selected_branch=Branch.query.get(selected_branch_id) if selected_branch_id else None,
        )

    @app.route("/receive-stock", methods=["GET", "POST"])
    @login_required
    def receive_stock():
        require_tenant_access(current_user.tenant_id)
        require_roles("tenant_owner", "branch_manager", "stock_clerk")
        branches = Branch.query.filter_by(tenant_id=current_user.tenant_id, status="active").all()
        suppliers = Supplier.query.filter_by(tenant_id=current_user.tenant_id).order_by(Supplier.name).all()
        products = Product.query.filter_by(tenant_id=current_user.tenant_id, status="active").order_by(Product.name).all()
        quick_products = Product.query.filter_by(tenant_id=current_user.tenant_id, status="active").order_by(Product.name).limit(12).all()

        if request.method == "POST":
            branch_id = request.form.get("branch_id", type=int)
            require_branch_access(branch_id)
            supplier_id = request.form.get("supplier_id", type=int)
            invoice_number = request.form.get("invoice_number", "").strip()
            note = request.form.get("note", "").strip()

            product_ids = request.form.getlist("product_id")
            quantities = request.form.getlist("quantity")
            costs = request.form.getlist("cost_price")

            purchase = Purchase(tenant_id=current_user.tenant_id, branch_id=branch_id, supplier_id=supplier_id,
                                invoice_number=invoice_number, note=note, created_by=current_user.id)
            db.session.add(purchase)
            db.session.flush()

            subtotal = 0
            for pid, qty, cost in zip(product_ids, quantities, costs):
                if not pid or not qty:
                    continue
                qty_f = float(qty)
                cost_f = float(cost or 0)
                line_total = qty_f * cost_f
                item = PurchaseItem(purchase_id=purchase.id, product_id=int(pid), quantity=qty_f, cost_price=cost_f, line_total=line_total)
                db.session.add(item)
                subtotal += line_total
                product = Product.query.get(int(pid))
                if product and cost_f > 0:
                    product.cost_price = cost_f
                move_inventory(current_user.tenant_id, branch_id, int(pid), qty_f, "purchase", "purchase", purchase.id, note, current_user.id)

            purchase.subtotal = subtotal
            purchase.grand_total = subtotal
            db.session.commit()
            audit("create", "purchase", purchase.id, f"Received stock invoice {invoice_number or purchase.id}", user_id=current_user.id, tenant_id=current_user.tenant_id, branch_id=branch_id)
            flash("Stock received successfully.", "success")
            return redirect(url_for("receive_stock"))
        return render_template("receive_stock.html", branches=branches, suppliers=suppliers, products=products)

    @app.route("/products/search")
    @login_required
    def product_search():
        require_tenant_access(current_user.tenant_id)
        require_roles("tenant_owner", "branch_manager", "stock_clerk", "cashier")
        q = (request.args.get("q") or "").strip()
        branch_id = request.args.get("branch_id", type=int)
        query = Product.query.filter_by(tenant_id=current_user.tenant_id, status="active")
        if q:
            like = f"%{q}%"
            query = query.filter(or_(
                Product.name.ilike(like),
                Product.barcode.ilike(like),
                Product.sku.ilike(like),
                Product.brand.ilike(like)
            ))
        results = []
        for product in query.order_by(Product.name).limit(20).all():
            stock_qty = None
            if branch_id:
                inv = Inventory.query.filter_by(tenant_id=current_user.tenant_id, branch_id=branch_id, product_id=product.id).first()
                stock_qty = inv.quantity if inv else 0
            results.append({
                "id": product.id,
                "name": product.name,
                "barcode": product.barcode or "",
                "sku": product.sku or "",
                "price": float(product.selling_price or 0),
                "stock": stock_qty,
            })
        from flask import jsonify
        return jsonify(results)

    @app.route("/pos", methods=["GET", "POST"])
    @login_required
    def pos():
        require_tenant_access(current_user.tenant_id)
        require_roles("tenant_owner", "branch_manager", "cashier")
        branches = Branch.query.filter_by(tenant_id=current_user.tenant_id, status="active").all()
        selected_branch_id = current_user.branch_id if current_user.role == "cashier" and current_user.branch_id else (request.args.get("branch_id", type=int) or (current_user.branch_id or (branches[0].id if branches else None)))
        if selected_branch_id:
            require_branch_access(selected_branch_id)
        products = Product.query.filter_by(tenant_id=current_user.tenant_id, status="active").order_by(Product.name).all()
        cart = get_cart()
        cart_items = []
        subtotal = 0
        for product_id, item in cart.items():
            product = Product.query.get(int(product_id))
            if product:
                line_total = item["qty"] * item["price"]
                cart_items.append({"product": product, "qty": item["qty"], "price": item["price"], "line_total": line_total})
                subtotal += line_total

        if request.method == "POST":
            action = request.form.get("action")
            if action == "add_item":
                pid = request.form.get("product_id", type=int)
                qty = request.form.get("qty", type=float) or 1
                product = Product.query.get_or_404(pid)
                if str(pid) in cart:
                    cart[str(pid)]["qty"] += qty
                else:
                    cart[str(pid)] = {"qty": qty, "price": product.selling_price}
                save_cart(cart)
                flash(f"Added {product.name} to cart.", "success")
                return redirect(url_for("pos", branch_id=selected_branch_id))
            elif action == "update_item":
                pid = request.form.get("product_id")
                qty = request.form.get("qty", type=float) or 0
                if pid in cart:
                    if qty <= 0:
                        cart.pop(pid)
                    else:
                        cart[pid]["qty"] = qty
                    save_cart(cart)
                return redirect(url_for("pos", branch_id=selected_branch_id))
            elif action == "clear_cart":
                save_cart({})
                flash("Cart cleared.", "warning")
                return redirect(url_for("pos", branch_id=selected_branch_id))
            elif action == "checkout":
                if not cart:
                    flash("Cart is empty.", "danger")
                    return redirect(url_for("pos", branch_id=selected_branch_id))
                payment_method = request.form.get("payment_method", "cash")
                customer_id = request.form.get("customer_id", type=int)
                note = request.form.get("note", "").strip()
                discount_total = request.form.get("discount_total", type=float) or 0
                sale_number = f"SALE-{int(datetime.utcnow().timestamp())}"
                sale = Sale(
                    tenant_id=current_user.tenant_id, branch_id=selected_branch_id, cashier_id=current_user.id,
                    customer_id=customer_id, sale_number=sale_number, status="completed", note=note
                )
                db.session.add(sale)
                db.session.flush()
                subtotal = 0
                tax_total = 0
                for product_id, item in cart.items():
                    product = Product.query.get(int(product_id))
                    inv = get_or_create_inventory(current_user.tenant_id, selected_branch_id, product.id)
                    if inv.quantity < item["qty"]:
                        flash(f"Not enough stock for {product.name}. Available: {inv.quantity}", "danger")
                        db.session.rollback()
                        return redirect(url_for("pos", branch_id=selected_branch_id))
                    line_total = item["qty"] * item["price"]
                    tax_amount = line_total * (product.tax_rate / 100.0)
                    sale_item = SaleItem(
                        sale_id=sale.id, product_id=product.id, product_name_snapshot=product.name,
                        barcode_snapshot=product.barcode, quantity=item["qty"], unit_price=item["price"],
                        discount_amount=0, tax_amount=tax_amount, line_total=line_total + tax_amount
                    )
                    db.session.add(sale_item)
                    subtotal += line_total
                    tax_total += tax_amount
                    move_inventory(current_user.tenant_id, selected_branch_id, product.id, -item["qty"], "sale", "sale", sale.id, "POS sale", current_user.id)
                sale.subtotal = subtotal
                sale.discount_total = discount_total
                sale.tax_total = tax_total
                sale.grand_total = subtotal + tax_total - discount_total
                db.session.add(SalePayment(sale_id=sale.id, payment_method=payment_method, amount=sale.grand_total))
                db.session.commit()
                save_cart({})
                audit("create", "sale", sale.id, f"Completed sale {sale.sale_number}", user_id=current_user.id, tenant_id=current_user.tenant_id, branch_id=selected_branch_id)
                flash("Sale completed.", "success")
                return redirect(url_for("receipt", sale_id=sale.id))
        customers = Customer.query.filter_by(tenant_id=current_user.tenant_id, status="active").order_by(Customer.name).all()
        quick_products = Product.query.filter_by(tenant_id=current_user.tenant_id, status="active").order_by(Product.name).limit(12).all()
        return render_template("pos.html", branches=branches, selected_branch_id=selected_branch_id, products=products,
                               quick_products=quick_products, cart_items=cart_items, subtotal=subtotal, customers=customers)

    @app.route("/receipt/<int:sale_id>")
    @login_required
    def receipt(sale_id):
        sale = Sale.query.get_or_404(sale_id)
        require_tenant_access(sale.tenant_id)
        setting = TenantSetting.query.filter_by(tenant_id=sale.tenant_id).first()
        tenant = Tenant.query.get(sale.tenant_id)
        branch = Branch.query.get(sale.branch_id)
        cashier = User.query.get(sale.cashier_id)
        can_reprint = current_user.is_super_admin or current_user.has_any_role("tenant_owner", "branch_manager")
        can_print = (not sale.is_printed) or can_reprint
        return render_template("receipt.html", sale=sale, tenant=tenant, branch=branch, cashier=cashier, setting=setting, can_reprint=can_reprint, can_print=can_print, print_mode=False)

    @app.route("/receipt/<int:sale_id>/print")
    @login_required
    def print_receipt(sale_id):
        sale = Sale.query.get_or_404(sale_id)
        require_tenant_access(sale.tenant_id)
        can_reprint = current_user.is_super_admin or current_user.has_any_role("tenant_owner", "branch_manager")
        if sale.is_printed and not can_reprint:
            flash("Receipt has already been printed once. Only manager or owner can reprint it.", "danger")
            return redirect(url_for("receipt", sale_id=sale.id))
        if sale.is_printed:
            sale.reprint_count = (sale.reprint_count or 0) + 1
            audit("reprint", "sale", sale.id, f"Reprinted receipt {sale.sale_number}", user_id=current_user.id, tenant_id=sale.tenant_id, branch_id=sale.branch_id)
        else:
            sale.is_printed = True
            sale.printed_at = datetime.utcnow()
            sale.printed_by = current_user.id
            audit("print", "sale", sale.id, f"Printed receipt {sale.sale_number}", user_id=current_user.id, tenant_id=sale.tenant_id, branch_id=sale.branch_id)
        db.session.commit()
        setting = TenantSetting.query.filter_by(tenant_id=sale.tenant_id).first()
        tenant = Tenant.query.get(sale.tenant_id)
        branch = Branch.query.get(sale.branch_id)
        cashier = User.query.get(sale.cashier_id)
        return render_template("receipt.html", sale=sale, tenant=tenant, branch=branch, cashier=cashier, setting=setting, can_reprint=can_reprint, can_print=True, print_mode=True)

    @app.route("/returns", methods=["GET", "POST"])
    @login_required
    def returns():
        require_tenant_access(current_user.tenant_id)
        require_roles("tenant_owner", "branch_manager")
        if request.method == "POST":
            sale_number = request.form.get("sale_number", "").strip()
            reason = request.form.get("reason", "").strip()
            refund_method = request.form.get("refund_method", "cash")
            sale = Sale.query.filter_by(tenant_id=current_user.tenant_id, sale_number=sale_number).first()
            if not sale:
                flash("Sale not found.", "danger")
                return redirect(url_for("returns"))
            ret = Return(tenant_id=sale.tenant_id, branch_id=sale.branch_id, sale_id=sale.id,
                         processed_by=current_user.id, refund_method=refund_method, reason=reason, refund_total=sale.grand_total)
            db.session.add(ret)
            db.session.flush()
            for item in sale.items:
                db.session.add(ReturnItem(return_id=ret.id, sale_item_id=item.id, quantity=item.quantity, refund_amount=item.line_total))
                move_inventory(sale.tenant_id, sale.branch_id, item.product_id, item.quantity, "return_in", "return", ret.id, reason, current_user.id)
            sale.status = "returned_full"
            db.session.commit()
            audit("return", "sale", sale.id, f"Returned sale {sale.sale_number}", user_id=current_user.id, tenant_id=sale.tenant_id, branch_id=sale.branch_id)
            flash("Return processed.", "success")
            return redirect(url_for("returns"))
        recent_sales = Sale.query.filter_by(tenant_id=current_user.tenant_id).order_by(Sale.created_at.desc()).limit(20).all()
        return render_template("returns.html", recent_sales=recent_sales)

    @app.route("/expenses", methods=["GET", "POST"])
    @login_required
    def expenses():
        require_tenant_access(current_user.tenant_id)
        require_roles("tenant_owner", "branch_manager")
        branches = Branch.query.filter_by(tenant_id=current_user.tenant_id).all()
        if request.method == "POST":
            branch_id = request.form.get("branch_id", type=int)
            expense = Expense(
                tenant_id=current_user.tenant_id,
                branch_id=branch_id,
                category=request.form.get("category", "").strip(),
                amount=request.form.get("amount", type=float) or 0,
                payment_method=request.form.get("payment_method", "cash"),
                note=request.form.get("note", "").strip(),
                expense_date=request.form.get("expense_date", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date()) if request.form.get("expense_date") else date.today(),
                created_by=current_user.id
            )
            db.session.add(expense)
            db.session.commit()
            audit("create", "expense", expense.id, f"Added expense {expense.category}", user_id=current_user.id, tenant_id=current_user.tenant_id, branch_id=branch_id)
            flash("Expense added.", "success")
            return redirect(url_for("expenses"))
        expense_rows = Expense.query.filter_by(tenant_id=current_user.tenant_id).order_by(Expense.expense_date.desc()).all()
        return render_template("expenses.html", expenses=expense_rows, branches=branches)

    @app.route("/transfers", methods=["GET", "POST"])
    @login_required
    def transfers():
        require_tenant_access(current_user.tenant_id)
        require_roles("tenant_owner", "branch_manager", "stock_clerk")
        branches = Branch.query.filter_by(tenant_id=current_user.tenant_id, status="active").all()
        products = Product.query.filter_by(tenant_id=current_user.tenant_id, status="active").all()
        if request.method == "POST":
            from_branch_id = request.form.get("from_branch_id", type=int)
            to_branch_id = request.form.get("to_branch_id", type=int)
            product_id = request.form.get("product_id", type=int)
            qty = request.form.get("quantity", type=float) or 0
            note = request.form.get("note", "").strip()
            if from_branch_id == to_branch_id:
                flash("Source and destination branch cannot be the same.", "danger")
                return redirect(url_for("transfers"))
            source = get_or_create_inventory(current_user.tenant_id, from_branch_id, product_id)
            if source.quantity < qty:
                flash("Not enough stock to transfer.", "danger")
                return redirect(url_for("transfers"))
            transfer = StockTransfer(tenant_id=current_user.tenant_id, from_branch_id=from_branch_id, to_branch_id=to_branch_id, note=note, created_by=current_user.id)
            db.session.add(transfer)
            db.session.flush()
            db.session.add(StockTransferItem(transfer_id=transfer.id, product_id=product_id, quantity=qty))
            move_inventory(current_user.tenant_id, from_branch_id, product_id, -qty, "transfer_out", "transfer", transfer.id, note, current_user.id)
            move_inventory(current_user.tenant_id, to_branch_id, product_id, qty, "transfer_in", "transfer", transfer.id, note, current_user.id)
            db.session.commit()
            audit("transfer", "stock", transfer.id, f"Transferred stock between branches", user_id=current_user.id, tenant_id=current_user.tenant_id)
            flash("Stock transferred.", "success")
            return redirect(url_for("transfers"))
        transfer_rows = StockTransfer.query.filter_by(tenant_id=current_user.tenant_id).order_by(StockTransfer.created_at.desc()).all()
        return render_template("transfers.html", transfers=transfer_rows, branches=branches, products=products)

    @app.route("/cashup", methods=["GET", "POST"])
    @login_required
    def cashup():
        require_tenant_access(current_user.tenant_id)
        require_roles("tenant_owner", "branch_manager", "cashier")
        branch_query = Branch.query.filter_by(tenant_id=current_user.tenant_id)
        if current_user.role in ["branch_manager", "cashier"] and current_user.branch_id:
            branch_query = branch_query.filter_by(id=current_user.branch_id)
        branches = branch_query.all()
        selected_branch_id = request.args.get("branch_id", type=int) or (current_user.branch_id if current_user.branch_id else (branches[0].id if branches else None))
        if selected_branch_id:
            require_branch_access(selected_branch_id)
        cashier_filter_id = current_user.id if current_user.role == "cashier" else None
        expected_cash = calculate_expected_cash(selected_branch_id, date.today(), cashier_filter_id) if selected_branch_id else 0.0
        if request.method == "POST":
            branch_id = request.form.get("branch_id", type=int)
            require_branch_access(branch_id)
            expected_cash = calculate_expected_cash(branch_id, date.today(), current_user.id if current_user.role == "cashier" else None)
            counted_cash = request.form.get("counted_cash", type=float) or 0
            note = request.form.get("note", "").strip()
            row = Cashup(
                tenant_id=current_user.tenant_id,
                branch_id=branch_id,
                cashier_id=current_user.id,
                expected_cash=expected_cash,
                actual_cash=counted_cash,
                counted_cash=counted_cash,
                variance=counted_cash - expected_cash,
                note=note
            )
            db.session.add(row)
            db.session.commit()
            audit("cashup", "cashup", row.id, "Completed cash-up", user_id=current_user.id, tenant_id=current_user.tenant_id, branch_id=branch_id)
            flash("Cash-up saved.", "success")
            return redirect(url_for("cashup", branch_id=branch_id))
        rows = Cashup.query.filter_by(tenant_id=current_user.tenant_id)
        if current_user.role == "cashier":
            rows = rows.filter_by(cashier_id=current_user.id)
        rows = rows.order_by(Cashup.created_at.desc()).all()
        return render_template("cashup.html", cashups=rows, branches=branches, selected_branch_id=selected_branch_id, expected_cash=expected_cash)

    @app.route("/reports")
    @login_required
    def reports():
        require_tenant_access(current_user.tenant_id)
        require_roles("tenant_owner", "branch_manager")
        tenant_id = current_user.tenant_id
        sales_total = db.session.query(func.coalesce(func.sum(Sale.grand_total), 0)).filter_by(tenant_id=tenant_id).scalar() or 0
        expenses_total = db.session.query(func.coalesce(func.sum(Expense.amount), 0)).filter_by(tenant_id=tenant_id).scalar() or 0
        purchases_total = db.session.query(func.coalesce(func.sum(Purchase.grand_total), 0)).filter_by(tenant_id=tenant_id).scalar() or 0
        sales_by_branch = db.session.query(Branch.name, func.coalesce(func.sum(Sale.grand_total), 0)).join(
            Sale, Sale.branch_id == Branch.id
        ).filter(Branch.tenant_id == tenant_id).group_by(Branch.name).all()
        low_stock = db.session.query(Product.name, func.coalesce(func.sum(Inventory.quantity), 0).label('qty')).join(
            Inventory, Inventory.product_id == Product.id
        ).filter(Product.tenant_id == tenant_id).group_by(Product.id).having(
            func.coalesce(func.sum(Inventory.quantity), 0) <= Product.reorder_level
        ).all()
        return render_template("reports.html", sales_total=sales_total, expenses_total=expenses_total,
                               purchases_total=purchases_total, sales_by_branch=sales_by_branch, low_stock=low_stock)

    @app.route("/settings", methods=["GET", "POST"])
    @login_required
    def settings():
        require_tenant_access(current_user.tenant_id)
        require_roles("tenant_owner", "branch_manager")
        setting = TenantSetting.query.filter_by(tenant_id=current_user.tenant_id).first()
        if request.method == "POST":
            setting.currency = request.form.get("currency", setting.currency)
            setting.receipt_footer = request.form.get("receipt_footer", setting.receipt_footer)
            setting.theme_color = request.form.get("theme_color", setting.theme_color)
            setting.support_phone = request.form.get("support_phone", setting.support_phone)
            setting.support_email = request.form.get("support_email", setting.support_email)
            setting.printer_name = request.form.get("printer_name", setting.printer_name)
            setting.paper_width_mm = request.form.get("paper_width_mm", type=int) or setting.paper_width_mm
            setting.auto_print_receipt = True if request.form.get("auto_print_receipt") == "on" else False
            db.session.commit()
            flash("Settings updated.", "success")
            return redirect(url_for("settings"))
        return render_template("settings.html", setting=setting)
