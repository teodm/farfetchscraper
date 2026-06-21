"""
Farfetch API client - estrae dati da Farfetch tramite le loro API interne
"""
import aiohttp
import asyncio
import json
import re
from typing import Optional, List, Dict

class FarfetchClient:
    BASE_URL = "https://www.farfetch.com"

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
    }

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None

    async def init(self):
        timeout = aiohttp.ClientTimeout(total=20)
        self.session = aiohttp.ClientSession(headers=self.HEADERS, timeout=timeout)

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    # ─── Utility: estrai JSON embedded nella pagina ────────────────────────────

    def _next_data(self, html: str) -> Optional[Dict]:
        m = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            html, re.DOTALL
        )
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                pass
        return None

    def _initial_state(self, html: str) -> Optional[Dict]:
        m = re.search(
            r'window\.__INITIAL_STATE__\s*=\s*({.+?});\s*(?:window|</script>)',
            html, re.DOTALL
        )
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                pass
        return None

    # ─── 1. PRODOTTO: stock per taglia/boutique ────────────────────────────────

    async def get_product(self, product_id: str) -> Optional[Dict]:
        """
        Restituisce un dict con:
          name, brand, price, image, url, sizes
          sizes = [{"size": "M", "boutiques": ["Boutique A", "Boutique B"], "available": True}, ...]
        """
        url = f"{self.BASE_URL}/it/shopping/item-{product_id}.aspx"
        async with self.session.get(url) as resp:
            if resp.status == 404:
                return None
            if resp.status != 200:
                raise ConnectionError(f"HTTP {resp.status} per ID {product_id}")
            html = await resp.text()

        # Prova prima __NEXT_DATA__
        nd = self._next_data(html)
        if nd:
            result = self._parse_product_nd(nd, product_id)
            if result:
                return result

        # Fallback __INITIAL_STATE__
        ist = self._initial_state(html)
        if ist:
            return self._parse_product_is(ist, product_id)

        return None

    def _parse_product_nd(self, data: Dict, pid: str) -> Optional[Dict]:
        try:
            pp = data["props"]["pageProps"]
            # Farfetch può usare più chiavi diverse nel tempo
            p = (
                pp.get("product") or
                pp.get("productData") or
                (pp.get("initialData") or {}).get("product") or
                {}
            )
            if not p:
                return None
            return self._build_product(p, pid)
        except Exception as e:
            print(f"[parse_product_nd] {e}")
            return None

    def _parse_product_is(self, data: Dict, pid: str) -> Optional[Dict]:
        try:
            p = (
                (data.get("product") or {}).get("detail") or
                data.get("PDPReducer") or
                {}
            )
            if not p:
                return None
            return self._build_product(p, pid)
        except Exception as e:
            print(f"[parse_product_is] {e}")
            return None

    def _build_product(self, p: Dict, pid: str) -> Dict:
        # Brand
        brand_obj = p.get("brand") or {}
        brand = brand_obj.get("name") if isinstance(brand_obj, dict) else str(brand_obj)

        # Prezzo
        price_obj = p.get("price") or p.get("priceInfo") or {}
        price = self._fmt_price(price_obj)

        # Immagine
        imgs = p.get("images") or []
        image = None
        if imgs:
            first = imgs[0]
            image = first if isinstance(first, str) else (
                first.get("url") or first.get("src") or first.get("thumbnailUrl")
            )

        # Taglie / stock
        sizes = self._extract_sizes(p)

        return {
            "id": pid,
            "name": p.get("name") or p.get("shortDescription") or "N/A",
            "brand": brand or "N/A",
            "price": price,
            "image": image,
            "url": f"https://www.farfetch.com/it/shopping/item-{pid}.aspx",
            "sizes": sizes,
        }

    def _fmt_price(self, obj: Dict) -> str:
        if not obj:
            return "N/A"
        v = (
            obj.get("formattedValue") or
            obj.get("formatted") or
            obj.get("value") or
            obj.get("amount")
        )
        if v is None:
            return "N/A"
        return v if isinstance(v, str) else f"€{float(v):.2f}"

    def _extract_sizes(self, p: Dict) -> List[Dict]:
        sizes = []
        variants = (
            p.get("variants") or
            p.get("sizes") or
            p.get("stockItems") or
            []
        )
        for v in variants:
            if not isinstance(v, dict):
                continue

            # Nome taglia
            size_val = v.get("size") or v.get("sizeName") or v.get("name") or {}
            size_name = size_val.get("name") if isinstance(size_val, dict) else str(size_val) if size_val else "N/A"

            # Boutique disponibili
            boutiques = []
            merchants = v.get("merchants") or v.get("sellers") or v.get("partners") or []
            for m in merchants:
                if isinstance(m, dict):
                    qty = m.get("stockQuantity") or m.get("stock") or 0
                    avail = m.get("available") or False
                    if qty > 0 or avail:
                        name = m.get("name") or m.get("merchantName") or "Boutique"
                        boutiques.append(name)

            # Caso in cui lo stock è direttamente sulla variante
            if not boutiques:
                qty = v.get("stock") or v.get("quantity") or 0
                if qty > 0:
                    seller = v.get("merchantName") or v.get("sellerName") or "Disponibile"
                    boutiques.append(seller)

            sizes.append({
                "size": size_name,
                "boutiques": boutiques,
                "available": len(boutiques) > 0,
            })
        return sizes

    # ─── 2. BOUTIQUE: tutti i prodotti ────────────────────────────────────────

    async def get_boutique_products(self, boutique_name: str) -> List[Dict]:
        """
        Cerca i prodotti di una boutique per nome.
        Prima tenta la ricerca per sellers ID, poi via URL slug.
        """
        slug = boutique_name.lower().strip().replace(" ", "-").replace("'", "")
        products = []
        seen_ids = set()

        for gender in ("women", "men"):
            url = f"{self.BASE_URL}/it/shopping/{gender}/{slug}/items.aspx"
            try:
                async with self.session.get(url) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        items = self._parse_listing(html)
                        for item in items:
                            if item["id"] not in seen_ids:
                                seen_ids.add(item["id"])
                                products.append(item)
            except Exception as e:
                print(f"[boutique/{gender}] {e}")
            await asyncio.sleep(0.5)

        # Se non trovato con lo slug, prova la ricerca generica
        if not products:
            products = await self._search_boutique_fallback(boutique_name, seen_ids)

        return products

    async def _search_boutique_fallback(self, boutique_name: str, seen_ids: set) -> List[Dict]:
        """Ricerca prodotti con il campo q= come fallback"""
        products = []
        url = f"{self.BASE_URL}/it/shopping/items.aspx"
        params = {"q": boutique_name, "view": "list"}
        try:
            async with self.session.get(url, params=params) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    items = self._parse_listing(html)
                    for item in items:
                        if item["id"] not in seen_ids:
                            seen_ids.add(item["id"])
                            products.append(item)
        except Exception as e:
            print(f"[boutique_fallback] {e}")
        return products

    def _parse_listing(self, html: str) -> List[Dict]:
        nd = self._next_data(html)
        if nd:
            items = self._items_from_nd(nd)
            if items:
                return items

        ist = self._initial_state(html)
        if ist:
            items = self._items_from_is(ist)
            if items:
                return items

        return []

    def _items_from_nd(self, data: Dict) -> List[Dict]:
        items = []
        try:
            pp = data["props"]["pageProps"]
            raw = (
                pp.get("items") or
                pp.get("products") or
                (pp.get("initialData") or {}).get("items") or
                (pp.get("listingData") or {}).get("items") or
                []
            )
            for r in raw:
                p = self._fmt_listing_item(r)
                if p:
                    items.append(p)
        except Exception as e:
            print(f"[items_from_nd] {e}")
        return items

    def _items_from_is(self, data: Dict) -> List[Dict]:
        items = []
        try:
            listing = data.get("listing") or data.get("ListingReducer") or {}
            raw = listing.get("items") or listing.get("products") or []
            for r in raw:
                p = self._fmt_listing_item(r)
                if p:
                    items.append(p)
        except Exception as e:
            print(f"[items_from_is] {e}")
        return items

    def _fmt_listing_item(self, item: Dict) -> Optional[Dict]:
        try:
            pid = str(item.get("id") or item.get("productId") or item.get("itemId") or "")
            if not pid:
                return None
            brand_obj = item.get("brand") or {}
            brand = brand_obj.get("name") if isinstance(brand_obj, dict) else str(brand_obj)
            price = self._fmt_price(
                item.get("price") or item.get("priceInfo") or {}
            )
            imgs = item.get("images") or []
            image = None
            if imgs:
                first = imgs[0]
                image = first if isinstance(first, str) else (
                    first.get("url") or first.get("src") or first.get("thumbnailUrl")
                )
            return {
                "id": pid,
                "name": item.get("name") or item.get("shortDescription") or "N/A",
                "brand": brand or "N/A",
                "price": price,
                "url": f"https://www.farfetch.com/it/shopping/item-{pid}.aspx",
                "image": image,
            }
        except Exception:
            return None
