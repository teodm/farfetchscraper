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
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
    }

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self._warmed_up = False

    async def init(self):
        timeout = aiohttp.ClientTimeout(total=25)
        # CookieJar di default mantiene i cookie tra le richieste,
        # fondamentale per superare i controlli anti-bot
        self.session = aiohttp.ClientSession(
            headers=self.HEADERS,
            timeout=timeout,
            cookie_jar=aiohttp.CookieJar(unsafe=True),
        )

    async def _warmup(self):
        """Visita la homepage prima della prima richiesta reale per
        ottenere i cookie di sessione necessari a superare le protezioni anti-bot."""
        if self._warmed_up:
            return
        try:
            async with self.session.get(f"{self.BASE_URL}/it/") as resp:
                await resp.read()
            self._warmed_up = True
            await asyncio.sleep(0.8)
        except Exception:
            pass  # se la warmup fallisce, proviamo comunque la richiesta reale

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

    async def get_product(self, identifier: str) -> Optional[Dict]:
        """
        Accetta sia un ID numerico (es. "34618362") sia un link Farfetch completo.
        Restituisce un dict con:
          name, brand, price, image, url, sizes
          sizes = [{"size": "M", "boutiques": ["Boutique A", "Boutique B"], "available": True}, ...]
        """
        await self._warmup()

        identifier = identifier.strip()
        product_id = self._extract_id(identifier)

        # Caso 1: l'utente ha incollato un link Farfetch completo → usalo direttamente
        if "farfetch.com" in identifier:
            page_url = identifier.split("?")[0]  # rimuove parametri di tracking
            html = await self._fetch_page(page_url)
            if html is None:
                return None
            return self._parse_html(html, product_id or "N/A", page_url)

        # Caso 2: solo l'ID → prova lo shortcut diretto
        if product_id:
            shortcut_url = f"{self.BASE_URL}/it/shopping/item-{product_id}.aspx"
            html = await self._fetch_page(shortcut_url, allow_404=True)
            if html is not None:
                return self._parse_html(html, product_id, shortcut_url)

            # Shortcut falliito (404) → cerca l'URL reale tramite la ricerca interna
            real_url = await self._resolve_url_via_search(product_id)
            if real_url:
                html = await self._fetch_page(real_url)
                if html is not None:
                    return self._parse_html(html, product_id, real_url)

        return None

    def _extract_id(self, text: str) -> Optional[str]:
        m = re.search(r"item-(\d+)\.aspx", text) or re.search(r"^(\d+)$", text)
        return m.group(1) if m else None

    async def _fetch_page(self, url: str, allow_404: bool = False) -> Optional[str]:
        headers = {"Referer": f"{self.BASE_URL}/it/"}
        async with self.session.get(url, headers=headers) as resp:
            if resp.status == 404:
                if allow_404:
                    return None
                return None
            if resp.status == 403:
                raise ConnectionError(
                    "Farfetch ha bloccato la richiesta (protezione anti-bot). "
                    "Riprova tra qualche secondo."
                )
            if resp.status != 200:
                raise ConnectionError(f"HTTP {resp.status} su {url}")
            return await resp.text()

    async def _resolve_url_via_search(self, product_id: str) -> Optional[str]:
        """Usa la ricerca interna di Farfetch per trovare l'URL completo (con slug)
        a partire dal solo ID prodotto."""
        search_url = f"{self.BASE_URL}/it/shopping/items.aspx"
        headers = {"Referer": f"{self.BASE_URL}/it/"}
        try:
            async with self.session.get(
                search_url, params={"q": product_id}, headers=headers
            ) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()
        except Exception:
            return None

        nd = self._next_data(html)
        if nd:
            found = self._deep_find_url_by_id(nd, product_id)
            if found:
                return found

        ist = self._initial_state(html)
        if ist:
            found = self._deep_find_url_by_id(ist, product_id)
            if found:
                return found

        return None

    def _deep_find_url_by_id(self, obj, pid: str, _depth: int = 0) -> Optional[str]:
        """Cerca ricorsivamente in una struttura JSON una stringa-URL che contenga
        sia '.aspx' sia l'ID prodotto richiesto (es. '...-item-34618362.aspx')."""
        if _depth > 12:
            return None
        if isinstance(obj, str):
            if f"item-{pid}.aspx" in obj or f"item-{pid}" in obj and ".aspx" in obj:
                if obj.startswith("http"):
                    return obj.split("?")[0]
                if obj.startswith("/"):
                    return (self.BASE_URL + obj).split("?")[0]
            return None
        if isinstance(obj, dict):
            for v in obj.values():
                res = self._deep_find_url_by_id(v, pid, _depth + 1)
                if res:
                    return res
            return None
        if isinstance(obj, list):
            for v in obj:
                res = self._deep_find_url_by_id(v, pid, _depth + 1)
                if res:
                    return res
            return None
        return None

    def _parse_html(self, html: str, pid: str, page_url: str) -> Optional[Dict]:
        nd = self._next_data(html)
        if nd:
            result = self._parse_product_nd(nd, pid)
            if result:
                result["url"] = page_url
                return result

        ist = self._initial_state(html)
        if ist:
            result = self._parse_product_is(ist, pid)
            if result:
                result["url"] = page_url
                return result

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
        await self._warmup()

        slug = boutique_name.lower().strip().replace(" ", "-").replace("'", "")
        products = []
        seen_ids = set()
        headers = {"Referer": f"{self.BASE_URL}/it/"}

        for gender in ("women", "men"):
            url = f"{self.BASE_URL}/it/shopping/{gender}/{slug}/items.aspx"
            try:
                async with self.session.get(url, headers=headers) as resp:
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
