"""Stable two-level household taxonomy."""

import sqlalchemy as sa
from alembic import op

revision = "003"
down_revision = "002"
branch_labels = depends_on = None

TAXONOMY = [
    (
        "fruit-vegetables",
        "Fruit & vegetables",
        [("fruit", "Fruit"), ("vegetables", "Vegetables"), ("fresh-herbs", "Fresh herbs")],
    ),
    (
        "meat-fish",
        "Meat & fish",
        [
            ("poultry", "Poultry"),
            ("beef", "Beef"),
            ("pork", "Pork"),
            ("fish", "Fish"),
            ("seafood", "Seafood"),
        ],
    ),
    (
        "dairy-eggs",
        "Dairy & eggs",
        [
            ("milk", "Milk"),
            ("yoghurt", "Yoghurt"),
            ("cheese", "Cheese"),
            ("butter", "Butter"),
            ("eggs", "Eggs"),
        ],
    ),
    (
        "store-cupboard",
        "Store cupboard",
        [
            ("pasta-rice", "Pasta & rice"),
            ("canned-foods", "Canned foods"),
            ("sauces", "Sauces"),
            ("stocks-seasonings", "Stocks & seasonings"),
        ],
    ),
    (
        "bread-bakery",
        "Bread & bakery",
        [("bread", "Bread"), ("wraps", "Wraps"), ("pastries", "Pastries")],
    ),
    (
        "prepared-meals",
        "Prepared meals",
        [
            ("soups", "Soups"),
            ("ready-meals", "Ready meals"),
            ("convenience-food", "Convenience food"),
        ],
    ),
    (
        "snacks-confectionery",
        "Snacks & confectionery",
        [("crisps", "Crisps"), ("biscuits", "Biscuits"), ("chocolate", "Chocolate")],
    ),
    (
        "drinks",
        "Drinks",
        [
            ("soft-drinks", "Soft drinks"),
            ("tea-coffee", "Tea & coffee"),
            ("alcoholic-drinks", "Alcoholic drinks"),
        ],
    ),
    (
        "household",
        "Household",
        [
            ("cleaning", "Cleaning"),
            ("laundry", "Laundry"),
            ("dishwasher", "Dishwasher"),
            ("paper-products", "Paper products"),
        ],
    ),
    (
        "personal-care",
        "Personal care",
        [("toiletries", "Toiletries"), ("hygiene", "Hygiene"), ("cosmetics", "Cosmetics")],
    ),
    ("other", "Other", []),
]


def upgrade():
    connection = op.get_bind()
    for slug, name, children in TAXONOMY:
        parent = connection.execute(
            sa.text("INSERT INTO categories (slug,name) VALUES (:slug,:name) RETURNING id"),
            dict(slug=slug, name=name),
        ).scalar_one()
        for child, label in children:
            connection.execute(
                sa.text(
                    "INSERT INTO categories (slug,name,parent_id) VALUES (:slug,:name,:parent)"
                ),
                dict(slug=slug + "." + child, name=label, parent=parent),
            )
    # Enforce two levels and reject cycles for future taxonomy edits as well.
    op.execute("""CREATE FUNCTION check_category_depth() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.parent_id IS NOT NULL AND (NEW.parent_id = NEW.id OR
         EXISTS (SELECT 1 FROM categories WHERE id = NEW.parent_id AND parent_id IS NOT NULL) OR
         EXISTS (SELECT 1 FROM categories WHERE parent_id = NEW.id)) THEN
        RAISE EXCEPTION 'category hierarchy limited to two levels';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER category_depth BEFORE INSERT OR UPDATE ON categories
    FOR EACH ROW EXECUTE FUNCTION check_category_depth();""")


def downgrade():
    op.execute("DROP TRIGGER category_depth ON categories; DROP FUNCTION check_category_depth()")
    op.execute(
        "UPDATE products SET category_id = NULL, classification_method = NULL, classified_at = NULL"
    )
    op.execute("DELETE FROM categories WHERE parent_id IS NOT NULL")
    op.execute("DELETE FROM categories")
