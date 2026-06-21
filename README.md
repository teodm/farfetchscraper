# 🛍️ Farfetch Discord Bot

Bot con due comandi slash per cercare prodotti su Farfetch.

---

## Comandi

| Comando | Cosa fa |
|---|---|
| `/prodotto <id>` | Per un ID Farfetch, mostra ogni taglia con la boutique che ce l'ha |
| `/boutique <nome>` | Elenca tutti i prodotti di una boutique (es. `Cenere`) |

---

## Setup

### 1. Crea il bot su Discord

1. Vai su <https://discord.com/developers/applications>
2. **New Application** → dai un nome
3. Sezione **Bot** → **Add Bot** → copia il **Token**
4. Sezione **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot Permissions: `Send Messages`, `Embed Links`
5. Copia il link generato e aprilo nel browser per invitare il bot al tuo server

### 2. Configurazione

```bash
# Clona / copia la cartella
cd farfetch-bot

# Crea il file .env
cp .env.example .env
# Modifica .env e incolla il token del bot
```

### 3. Installa le dipendenze

```bash
pip install -r requirements.txt
```

### 4. Avvia

```bash
python main.py
```

Il bot sincronizza i comandi all'avvio.  
Se hai impostato `GUILD_ID`, i comandi appaiono subito nel tuo server;
senza, possono volerci fino a 60 minuti per la sincronizzazione globale.

---

## Note tecniche

Il bot effettua scraping della pagina prodotto di Farfetch estraendo i dati
JSON embedded (`__NEXT_DATA__` / `__INITIAL_STATE__`).  
Se Farfetch modifica la struttura della pagina, potrebbe essere necessario
aggiornare i parser in `farfetch_client.py`.

### Struttura file

```
farfetch-bot/
├── main.py            ← bot Discord e comandi slash
├── farfetch_client.py ← client HTTP per Farfetch
├── requirements.txt
├── .env.example
└── README.md
```
