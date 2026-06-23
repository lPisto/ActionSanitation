import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from pymongo import UpdateOne


BACKEND_DIR = Path(__file__).resolve().parent
JSON_FILE_PATH = BACKEND_DIR / "products_mongo_seed_with_sds.json"
COLLECTION_NAME = "products_metadata"

# Make app imports and .env loading work whether the script is launched from
# Backend or from the repository root.
os.chdir(BACKEND_DIR)
sys.path.append(str(BACKEND_DIR))

from app.db.mongodb import close_mongo_connection, connect_to_mongo, get_database


def load_subcategory_updates(skip_empty: bool) -> list[tuple[str, list]]:
    if not JSON_FILE_PATH.exists():
        raise FileNotFoundError(f"No se encontro el archivo {JSON_FILE_PATH}.")

    with JSON_FILE_PATH.open("r", encoding="utf-8") as f:
        products = json.load(f)

    if not isinstance(products, list):
        raise ValueError("El archivo JSON debe contener una lista de productos.")

    updates: list[tuple[str, list]] = []
    seen_skus: set[str] = set()

    for index, item in enumerate(products, start=1):
        if not isinstance(item, dict):
            print(f"Saltando item #{index}: no es un objeto JSON.")
            continue

        sku = item.get("sku")
        subcategories = item.get("subcategories")

        if not sku:
            print(f"Saltando item #{index}: no tiene sku.")
            continue
        if sku in seen_skus:
            print(f"Saltando sku duplicado {sku!r}.")
            continue
        if not isinstance(subcategories, list):
            print(f"Saltando sku {sku!r}: subcategories no es una lista.")
            continue
        if skip_empty and not subcategories:
            continue

        seen_skus.add(sku)
        updates.append((str(sku), subcategories))

    return updates


async def update_subcategories(
    dry_run: bool,
    skip_empty: bool,
    upsert: bool,
    batch_size: int,
) -> None:
    updates = load_subcategory_updates(skip_empty=skip_empty)
    non_empty = sum(1 for _, subcategories in updates if subcategories)
    empty = len(updates) - non_empty

    print(f"Leyendo datos desde {JSON_FILE_PATH.name}...", flush=True)
    print(
        f"Listos para procesar {len(updates)} SKUs "
        f"({non_empty} con subcategories, {empty} vacios).",
        flush=True,
    )

    if dry_run:
        print("Dry run: no se actualizo MongoDB.", flush=True)
        return

    try:
        await connect_to_mongo()
        db = get_database()
        collection = db[COLLECTION_NAME]

        matched = 0
        modified = 0
        missing = 0
        upserted = 0
        processed = 0

        for start in range(0, len(updates), batch_size):
            batch = updates[start : start + batch_size]
            operations = [
                UpdateOne(
                    {"sku": sku},
                    {"$set": {"subcategories": subcategories}},
                    upsert=upsert,
                )
                for sku, subcategories in batch
            ]

            result = await collection.bulk_write(operations, ordered=False)

            processed += len(batch)
            matched += result.matched_count
            modified += result.modified_count
            upserted += result.upserted_count
            missing += len(batch) - result.matched_count - result.upserted_count

            print(f"  -> Procesados {processed} productos...", flush=True)

        print(
            "Hecho. "
            f"Procesados={len(updates)}, matched={matched}, modified={modified}, "
            f"missing={missing}, upserted={upserted}.",
            flush=True,
        )
    finally:
        await close_mongo_connection()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Actualiza solo el campo subcategories en products_metadata "
            "usando products_mongo_seed_with_sds.json."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Valida el JSON y muestra cuantos SKUs se procesarian sin tocar MongoDB.",
    )
    parser.add_argument(
        "--skip-empty",
        action="store_true",
        help="No actualiza SKUs cuyo subcategories sea una lista vacia.",
    )
    parser.add_argument(
        "--upsert",
        action="store_true",
        help="Crea documentos faltantes con sku y subcategories. Por defecto solo actualiza existentes.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Cantidad de productos por lote de bulk_write. Por defecto: 500.",
    )
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size debe ser mayor que 0.")
    return args


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(
        update_subcategories(
            dry_run=args.dry_run,
            skip_empty=args.skip_empty,
            upsert=args.upsert,
            batch_size=args.batch_size,
        )
    )
