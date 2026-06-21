"""
Bot Discord per Farfetch
Comandi:
  /prodotto <id>       → disponibilità per taglia/boutique
  /boutique  <nome>    → tutti i prodotti di una boutique
"""
import asyncio
import os
import textwrap

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from farfetch_client import FarfetchClient

load_dotenv()

TOKEN    = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")   # opzionale – accelera la registrazione dei comandi

# ─── Setup bot ────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
bot     = commands.Bot(command_prefix="!", intents=intents)
farfetch: FarfetchClient | None = None

# ─── Colori embed ─────────────────────────────────────────────────────────────
COLOR_OK  = 0x000000   # nero (stile Farfetch)
COLOR_ERR = 0xE74C3C   # rosso

# ─── Evento: bot pronto ───────────────────────────────────────────────────────

@bot.event
async def on_ready():
    global farfetch
    farfetch = FarfetchClient()
    await farfetch.init()

    if GUILD_ID:
        guild = discord.Object(id=int(GUILD_ID))
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    else:
        await bot.tree.sync()

    print(f"✅ {bot.user} online – comandi sincronizzati")

@bot.event
async def on_close():
    if farfetch:
        await farfetch.close()

# ─── /prodotto ────────────────────────────────────────────────────────────────

@bot.tree.command(
    name="prodotto",
    description="Mostra disponibilità per taglia dato un ID prodotto Farfetch"
)
@app_commands.describe(product_id="ID del prodotto su Farfetch (es. 21894573)")
async def cmd_prodotto(interaction: discord.Interaction, product_id: str):
    await interaction.response.defer(thinking=True)

    product_id = product_id.strip()
    if not product_id.isdigit():
        await interaction.followup.send(
            embed=_err_embed("ID non valido", "L'ID deve essere numerico (es. `21894573`).")
        )
        return

    try:
        data = await farfetch.get_product(product_id)
    except Exception as e:
        await interaction.followup.send(embed=_err_embed("Errore di rete", str(e)))
        return

    if not data:
        await interaction.followup.send(
            embed=_err_embed("Prodotto non trovato", f"Nessun risultato per ID **{product_id}**.")
        )
        return

    embed = discord.Embed(
        title=f"{data['brand']} – {data['name']}",
        url=data["url"],
        color=COLOR_OK,
    )
    embed.add_field(name="💰 Prezzo", value=data["price"], inline=True)
    embed.add_field(name="🆔 ID", value=f"`{product_id}`", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=False)

    sizes = data.get("sizes") or []
    if not sizes:
        embed.add_field(
            name="📦 Disponibilità",
            value="*Nessuna informazione sulle taglie*",
            inline=False,
        )
    else:
        lines = []
        for s in sizes:
            size_label = s["size"]
            boutiques  = s["boutiques"]
            if boutiques:
                bstr = " · ".join(boutiques[:5])   # max 5 per riga
                if len(boutiques) > 5:
                    bstr += f" (+{len(boutiques)-5})"
                lines.append(f"**{size_label}** → {bstr}")
            else:
                lines.append(f"~~{size_label}~~ — esaurita")

        # Discord limita i field a 1024 char
        chunk = "\n".join(lines)
        for piece in _split_text(chunk, 1020):
            embed.add_field(name="🏷️ Taglia → Boutique", value=piece, inline=False)

    if data.get("image"):
        embed.set_thumbnail(url=data["image"])

    embed.set_footer(text="Dati Farfetch • potrebbe non essere in tempo reale")
    await interaction.followup.send(embed=embed)

# ─── /boutique ────────────────────────────────────────────────────────────────

@bot.tree.command(
    name="boutique",
    description="Elenca tutti i prodotti di una boutique Farfetch"
)
@app_commands.describe(nome="Nome della boutique (es. Cenere, Antonioli, ...)")
async def cmd_boutique(interaction: discord.Interaction, nome: str):
    await interaction.response.defer(thinking=True)

    nome = nome.strip()
    if not nome:
        await interaction.followup.send(
            embed=_err_embed("Nome mancante", "Specifica il nome della boutique.")
        )
        return

    try:
        products = await farfetch.get_boutique_products(nome)
    except Exception as e:
        await interaction.followup.send(embed=_err_embed("Errore di rete", str(e)))
        return

    if not products:
        await interaction.followup.send(
            embed=_err_embed(
                "Boutique non trovata",
                f"Nessun prodotto per **{nome}**.\n"
                "Controlla che il nome sia esatto (es. `Cenere`, `Antonioli`)."
            )
        )
        return

    # Primo embed: intestazione + primi 10 prodotti
    total   = len(products)
    batches = [products[i:i+10] for i in range(0, min(total, 50), 10)]

    for idx, batch in enumerate(batches):
        embed = discord.Embed(
            title=f"🏪 {nome}",
            color=COLOR_OK,
        )
        if idx == 0:
            embed.description = f"**{total}** prodotti trovati"

        for p in batch:
            name_label = textwrap.shorten(f"{p['brand']} – {p['name']}", width=60, placeholder="…")
            embed.add_field(
                name=name_label,
                value=f"💰 {p['price']}\n🔗 [Vai al prodotto]({p['url']})",
                inline=False,
            )

        embed.set_footer(text=f"Pagina {idx+1}/{len(batches)} · Farfetch")

        if idx == 0:
            await interaction.followup.send(embed=embed)
        else:
            await interaction.channel.send(embed=embed)   # type: ignore
            await asyncio.sleep(0.3)

# ─── Helper ───────────────────────────────────────────────────────────────────

def _err_embed(title: str, desc: str) -> discord.Embed:
    return discord.Embed(title=f"❌ {title}", description=desc, color=COLOR_ERR)

def _split_text(text: str, limit: int = 1020):
    """Divide il testo in pezzi da max `limit` caratteri (per i field di Discord)."""
    lines  = text.split("\n")
    chunks = []
    current = ""
    for line in lines:
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current = current + "\n" + line if current else line
    if current:
        chunks.append(current)
    return chunks

# ─── Entry point ──────────────────────────────────────────────────────────────

async def main():
    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
