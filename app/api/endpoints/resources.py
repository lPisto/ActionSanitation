from fastapi import APIRouter

router = APIRouter()

@router.get("/downloads/catalogs")
async def get_catalogs():
    return [{"id": 1, "name": "2026 Catalog", "url": "/static/catalogs/2026_catalog.pdf"}]

@router.get("/downloads/flyers")
async def get_flyers():
    return [{"id": 1, "name": "Spring Sale Flyer", "url": "/static/flyers/spring_sale.pdf"}]

@router.get("/downloads/sds")
async def get_sds():
    return [{"id": 1, "name": "Bleach SDS", "url": "/static/sds/bleach_sds.pdf"}]

@router.get("/gallery")
async def get_gallery():
    return [{"id": 1, "title": "Warehouse", "image_url": "/static/gallery/warehouse.jpg"}]

@router.get("/training")
async def get_training_materials():
    return [{"id": 1, "title": "Product Usage Guide", "video_url": "https://example.com/video"}]
