"""商品管理 API：列表 / 详情 / 创建 / 更新 / 上下架 / 删除。"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from mock_merchant import auth, db, rules
from mock_merchant.response import AppError, ok
from mock_merchant.routes import helpers

router = APIRouter(prefix="/products", tags=["商品管理"])


class ProductCreate(BaseModel):
    sku: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    category: str = ""
    price: float = Field(gt=0)
    original_price: float = 0
    highlights: str = ""
    description: str = ""
    image_url: str = ""
    sizes: dict = {}
    colors: list = []
    scenes: list = []
    link: str = ""
    stock: int = Field(default=0, ge=0)
    safety_stock: int = Field(default=10, ge=0)


class ProductUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    price: float | None = Field(default=None, gt=0)
    original_price: float | None = None
    highlights: str | None = None
    description: str | None = None
    image_url: str | None = None
    sizes: dict | None = None
    colors: list | None = None
    scenes: list | None = None
    link: str | None = None
    stock: int | None = Field(default=None, ge=0)
    safety_stock: int | None = Field(default=None, ge=0)


class ProductStatus(BaseModel):
    status: str  # 在售 / 下架


def _get_product(sku: str) -> dict:
    row = db.query_one("SELECT * FROM products WHERE sku = ?", (sku,))
    if not row:
        raise AppError(f"商品 {sku} 不存在", "PRODUCT_NOT_FOUND", 404)
    return row


@router.get("")
def list_products(
    keyword: str = Query(default="", description="按名称/SKU/分类模糊搜索"),
    category: str = "",
    status: str = "",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    principal: dict = Depends(auth.require_scope("products:read")),
):
    """商品列表（支持关键词搜索、分类与状态筛选、分页）。"""
    where, params = ["1=1"], []
    if keyword:
        where.append("(name LIKE ? OR sku LIKE ? OR category LIKE ?)")
        params += [f"%{keyword}%"] * 3
    if category:
        where.append("category = ?")
        params.append(category)
    if status:
        where.append("status = ?")
        params.append(status)
    cond = " AND ".join(where)
    total = db.query_one(f"SELECT COUNT(*) AS n FROM products WHERE {cond}", params)["n"]
    rows = db.query(
        f"""SELECT * FROM products WHERE {cond}
            ORDER BY id DESC LIMIT ? OFFSET ?""",
        params + [page_size, (page - 1) * page_size],
    )
    return ok({
        "list": [helpers.product_dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    })


@router.get("/{sku}")
def get_product(sku: str, principal: dict = Depends(auth.require_scope("products:read"))):
    """商品详情（含库存、尺码、卖点、场景等 V4 兼容字段）。"""
    return ok(helpers.product_dict(_get_product(sku)))


@router.post("")
def create_product(body: ProductCreate,
                   principal: dict = Depends(auth.require_scope("products:write"))):
    """创建商品（商家运营/管理员）。"""
    if db.query_one("SELECT 1 AS x FROM products WHERE sku = ?", (body.sku,)):
        raise AppError(f"SKU {body.sku} 已存在", "SKU_DUPLICATED", 409)
    now = rules.now_str()
    stock = body.stock if body.stock else sum(
        v for v in body.sizes.values() if isinstance(v, int)
    )
    db.execute(
        """INSERT INTO products (sku, name, category, price, original_price, highlights,
           description, image_url, sizes, colors, scenes, link, stock, locked_stock,
           safety_stock, sales, status, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?, ?,?,0,?,0,'在售',?,?)""",
        (body.sku, body.name, body.category, body.price,
         body.original_price or body.price, body.highlights, body.description,
         body.image_url, db.dumps(body.sizes), db.dumps(body.colors),
         db.dumps(body.scenes), body.link or f"https://shop.demo.cn/goods/{body.sku}",
         stock, body.safety_stock, now, now),
    )
    helpers.log_op(principal, "创建商品", f"products:{body.sku}",
                   f"新增商品 {body.name}（¥{body.price}，库存 {stock}）")
    return ok(helpers.product_dict(_get_product(body.sku)), "商品创建成功", 201)


@router.put("/{sku}")
def update_product(sku: str, body: ProductUpdate,
                   principal: dict = Depends(auth.require_scope("products:write"))):
    """更新商品信息（未传字段保持不变）。"""
    _get_product(sku)
    fields = {
        "name": body.name, "category": body.category, "price": body.price,
        "original_price": body.original_price, "highlights": body.highlights,
        "description": body.description, "image_url": body.image_url,
        "sizes": db.dumps(body.sizes) if body.sizes is not None else None,
        "colors": db.dumps(body.colors) if body.colors is not None else None,
        "scenes": db.dumps(body.scenes) if body.scenes is not None else None,
        "link": body.link, "safety_stock": body.safety_stock,
    }
    updates = [f"{k} = ?" for k, v in fields.items() if v is not None]
    params = [v for v in fields.values() if v is not None]
    if body.stock is not None:
        row = db.query_one("SELECT stock, locked_stock FROM products WHERE sku = ?", (sku,))
        if body.stock < (row["locked_stock"] if row else 0):
            raise AppError(
                f"库存不能低于已锁定数量（{row['locked_stock']}）", "INVALID_STOCK", 409)
        updates.append("stock = ?")
        params.append(body.stock)
    if updates:
        updates.append("updated_at = ?")
        params.append(rules.now_str())
        params.append(sku)
        db.execute(f"UPDATE products SET {', '.join(updates)} WHERE sku = ?", params)
    helpers.log_op(principal, "更新商品", f"products:{sku}", "修改商品信息")
    return ok(helpers.product_dict(_get_product(sku)), "商品更新成功")


@router.patch("/{sku}/status")
def update_status(sku: str, body: ProductStatus,
                  principal: dict = Depends(auth.require_scope("products:write"))):
    """商品上下架（在售 / 下架）。"""
    _get_product(sku)
    if body.status not in ("在售", "下架"):
        raise AppError("商品状态仅支持：在售、下架", "INVALID_STATUS", 400)
    db.execute(
        "UPDATE products SET status = ?, updated_at = ? WHERE sku = ?",
        (body.status, rules.now_str(), sku),
    )
    helpers.log_op(principal, "商品上下架", f"products:{sku}", f"商品状态变更为 {body.status}")
    return ok(helpers.product_dict(_get_product(sku)), f"商品已{body.status}")


@router.delete("/{sku}")
def delete_product(sku: str,
                   principal: dict = Depends(auth.require_scope("products:write"))):
    """删除商品（仅管理员；已有订单引用的商品不允许删除）。"""
    _get_product(sku)
    if principal["role"] != "merchant_admin":
        raise AppError("删除商品仅商家管理员可操作", "FORBIDDEN", 403)
    used = db.query_one(
        "SELECT 1 AS x FROM orders WHERE items LIKE ? LIMIT 1", (f'%"{sku}"%',)
    )
    if used:
        raise AppError(f"商品 {sku} 已被订单引用，无法删除，建议下架", "PRODUCT_IN_USE", 409)
    db.execute("DELETE FROM products WHERE sku = ?", (sku,))
    helpers.log_op(principal, "删除商品", f"products:{sku}", f"删除商品 {sku}")
    return ok(None, "商品已删除")
