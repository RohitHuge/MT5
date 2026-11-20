//+------------------------------------------------------------------+
//| MT5Bridge.mq5                                                    |
//| Lightweight HTTP server EA — exposes MT5 data and trading over   |
//| TCP so mt5-bridge (FastAPI, Ubuntu Docker) can call it instead   |
//| of the Windows-only MetaTrader5 Python package.                  |
//|                                                                  |
//| Uses ws2_32.dll directly for server socket (MQL5 built-in        |
//| Socket* functions are client-only; no SocketListen/Accept).      |
//|                                                                  |
//| Endpoints:                                                       |
//|   GET  /health                                                   |
//|   GET  /account                                                  |
//|   GET  /symbol/{symbol}                                          |
//|   GET  /candles?symbol=X&timeframe=Y&count=N                     |
//|   GET  /positions                                                |
//|   POST /order                                                    |
//|   POST /close/{ticket}                                           |
//|                                                                  |
//| Deploy: copy to MQL5/Experts/, compile, attach to any chart.    |
//| Requires: Tools > Options > Expert Advisors > Allow DLL imports  |
//+------------------------------------------------------------------+
#property copyright "MT5 Platform"
#property version   "2.00"
#property strict

// ─────────────────────────────────────────────────────────────────────────────
// Windows Sockets 2 — server socket via DLL import
// ─────────────────────────────────────────────────────────────────────────────

#import "ws2_32.dll"
   int    socket(int af, int type, int protocol);
   int    closesocket(int s);
   int    bind(int s, uchar &addr[], int namelen);
   int    listen(int s, int backlog);
   int    accept(int s, uchar &addr[], int &addrlen);
   int    recv(int s, uchar &buf[], int len, int flags);
   int    send(int s, uchar &buf[], int len, int flags);
   int    WSAStartup(ushort ver, uchar &data[]);
   int    WSACleanup();
   int    ioctlsocket(int s, uint cmd, uint &argp);
   int    setsockopt(int s, int level, int optname, uchar &optval[], int optlen);
   int    WSAGetLastError();
#import

#define AF_INET        2
#define SOCK_STREAM    1
#define IPPROTO_TCP    6
#define SOL_SOCKET     0xFFFF
#define SO_RCVTIMEO    0x1005
#define FIONBIO        0x8004667E
#define INVALID_SOCK   (-1)
#define WSAEWOULDBLOCK 10035

input int    Port         = 8002;   // TCP port to listen on
input int    PollMs       = 1000;   // OnTimer interval (ms)
input int    Magic        = 234001; // Magic number for all orders
input int    Deviation    = 50;     // Max price deviation (points)

#define CRLF "\r\n"

int _server = INVALID_SOCK;

// ─────────────────────────────────────────────────────────────────────────────
// Lifecycle
// ─────────────────────────────────────────────────────────────────────────────

int OnInit()
{
   uchar wsaData[408];
   if(WSAStartup(0x0202, wsaData) != 0)
   {
      Print("MT5Bridge: WSAStartup failed: ", WSAGetLastError());
      return INIT_FAILED;
   }

   _server = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
   if(_server == INVALID_SOCK)
   {
      Print("MT5Bridge: socket() failed: ", WSAGetLastError());
      return INIT_FAILED;
   }

   // Non-blocking server so OnTimer returns immediately when no connection
   uint nb = 1;
   ioctlsocket(_server, FIONBIO, nb);

   // Build sockaddr_in (16 bytes): family=AF_INET, port=Port, addr=0.0.0.0
   uchar addr[16];
   ArrayInitialize(addr, 0);
   addr[0] = 2; addr[1] = 0;                       // sin_family = AF_INET
   addr[2] = (uchar)(Port >> 8);                    // sin_port hi byte
   addr[3] = (uchar)(Port & 0xFF);                  // sin_port lo byte
   // addr[4..7] = 0 → INADDR_ANY (all interfaces)

   if(bind(_server, addr, 16) != 0)
   {
      Print("MT5Bridge: bind() failed: ", WSAGetLastError());
      closesocket(_server);
      return INIT_FAILED;
   }

   if(listen(_server, 10) != 0)
   {
      Print("MT5Bridge: listen() failed: ", WSAGetLastError());
      closesocket(_server);
      return INIT_FAILED;
   }

   Print("MT5Bridge: listening on port ", Port);
   EventSetMillisecondTimer(PollMs);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   if(_server != INVALID_SOCK) { closesocket(_server); _server = INVALID_SOCK; }
   WSACleanup();
   Print("MT5Bridge: stopped");
}

void OnTick() {}

void OnTimer()
{
   uchar addrBuf[16];
   int   addrLen = 16;
   int   client  = accept(_server, addrBuf, addrLen);
   if(client == INVALID_SOCK) return; // WSAEWOULDBLOCK — no pending connection

   // 2-second recv timeout on client socket
   uchar tv[4] = {0xD0, 0x07, 0x00, 0x00}; // 2000ms in little-endian
   setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, tv, 4);

   string raw = ReadRequest(client);
   if(raw != "")
   {
      string method, path, body;
      ParseRequest(raw, method, path, body);
      string response = RouteRequest(method, path, body);
      uchar  buf[];
      StringToCharArray(response, buf, 0, StringLen(response));
      send(client, buf, ArraySize(buf), 0);
   }
   closesocket(client);
}

// ─────────────────────────────────────────────────────────────────────────────
// Request reading
// ─────────────────────────────────────────────────────────────────────────────

string ReadRequest(int client)
{
   string result = "";
   uchar  buf[4096];

   while(true)
   {
      ArrayInitialize(buf, 0);
      int n = recv(client, buf, ArraySize(buf) - 1, 0);
      if(n <= 0) break;
      result += CharArrayToString(buf, 0, n);
      if(IsRequestComplete(result)) break;
   }
   return result;
}

bool IsRequestComplete(string raw)
{
   // Headers end at double CRLF
   int headerEnd = StringFind(raw, CRLF + CRLF);
   if(headerEnd < 0)
      return false;

   // For POST, check we have Content-Length bytes of body too
   string lower = raw;
   StringToLower(lower);
   int clPos = StringFind(lower, "content-length:");
   if(clPos < 0)
      return true;  // GET — headers only

   int valStart = clPos + 15;
   int lineEnd  = StringFind(raw, CRLF, valStart);
   int cl       = (int)StringToInteger(StringSubstr(raw, valStart, lineEnd - valStart));
   int bodyStart = headerEnd + 4;
   return StringLen(raw) - bodyStart >= cl;
}

// ─────────────────────────────────────────────────────────────────────────────
// Request parsing
// ─────────────────────────────────────────────────────────────────────────────

void ParseRequest(string raw, string &method, string &path, string &body)
{
   method = "";
   path   = "";
   body   = "";

   int sp1 = StringFind(raw, " ");
   if(sp1 < 0) return;
   method = StringSubstr(raw, 0, sp1);

   int sp2 = StringFind(raw, " ", sp1 + 1);
   if(sp2 < 0) return;
   path = StringSubstr(raw, sp1 + 1, sp2 - sp1 - 1);

   int headerEnd = StringFind(raw, CRLF + CRLF);
   if(headerEnd >= 0)
      body = StringSubstr(raw, headerEnd + 4);
}

// ─────────────────────────────────────────────────────────────────────────────
// Router
// ─────────────────────────────────────────────────────────────────────────────

string RouteRequest(string method, string path, string body)
{
   string pathOnly = path;
   string query    = "";
   int    qmark    = StringFind(path, "?");
   if(qmark >= 0)
   {
      pathOnly = StringSubstr(path, 0, qmark);
      query    = StringSubstr(path, qmark + 1);
   }

   string json = "";

   if(pathOnly == "/health" && method == "GET")
      json = HandleHealth();
   else if(pathOnly == "/account" && method == "GET")
      json = HandleAccount();
   else if(pathOnly == "/candles" && method == "GET")
      json = HandleCandles(query);
   else if(pathOnly == "/positions" && method == "GET")
      json = HandlePositions();
   else if(StringFind(pathOnly, "/symbol/") == 0 && method == "GET")
      json = HandleSymbol(StringSubstr(pathOnly, 8));
   else if(StringFind(pathOnly, "/close/") == 0 && method == "POST")
      json = HandleClose((long)StringToInteger(StringSubstr(pathOnly, 7)));
   else if(pathOnly == "/order" && method == "POST")
      json = HandleOrder(body);
   else
      return HttpResponse(404, "{\"error\":\"not found\"}");

   return HttpResponse(200, json);
}

// ─────────────────────────────────────────────────────────────────────────────
// Handlers
// ─────────────────────────────────────────────────────────────────────────────

string HandleHealth()
{
   bool connected = (bool)TerminalInfoInteger(TERMINAL_CONNECTED);
   int  build     = (int)TerminalInfoInteger(TERMINAL_BUILD);
   string now     = TimeToString(TimeGMT(), TIME_DATE | TIME_SECONDS);

   return "{" +
      JStr("status",          connected ? "ok" : "error") + "," +
      JBool("terminal_connected", connected)               + "," +
      JInt("terminal_build",  build)                       + "," +
      JStr("server_time_utc", now)                         +
   "}";
}

string HandleAccount()
{
   long   login  = AccountInfoInteger(ACCOUNT_LOGIN);
   double bal    = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double margin = AccountInfoDouble(ACCOUNT_MARGIN);
   double free   = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   double level  = AccountInfoDouble(ACCOUNT_MARGIN_LEVEL);
   string cur    = AccountInfoString(ACCOUNT_CURRENCY);

   return "{" +
      JInt("login",           login)      + "," +
      JDbl("balance",         bal,    2)  + "," +
      JDbl("equity",          equity, 2)  + "," +
      JDbl("margin",          margin, 2)  + "," +
      JDbl("free_margin",     free,   2)  + "," +
      JDbl("margin_level_pct",level,  2)  + "," +
      JStr("currency",        cur)        +
   "}";
}

string HandleSymbol(string symbol)
{
   if(!SymbolSelect(symbol, true))
      return "{\"error\":\"symbol not found\"}";

   double point    = SymbolInfoDouble(symbol, SYMBOL_POINT);
   int    spread   = (int)SymbolInfoInteger(symbol, SYMBOL_SPREAD);
   double volMin   = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double volMax   = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double volStep  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   double tickVal  = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSz   = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   double contract = SymbolInfoDouble(symbol, SYMBOL_TRADE_CONTRACT_SIZE);
   int    digits   = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   long   tradeMode= SymbolInfoInteger(symbol, SYMBOL_TRADE_MODE);

   return "{" +
      JStr("symbol",        symbol)              + "," +
      JDbl("point",         point,      digits+1) + "," +
      JInt("spread",        spread)              + "," +
      JDbl("volume_min",    volMin,     2)        + "," +
      JDbl("volume_max",    volMax,     2)        + "," +
      JDbl("volume_step",   volStep,    2)        + "," +
      JDbl("tick_value",    tickVal,    5)        + "," +
      JDbl("tick_size",     tickSz,     digits+1) + "," +
      JDbl("contract_size", contract,   2)        + "," +
      JInt("digits",        digits)              + "," +
      JBool("trade_allowed",tradeMode != 0)       +
   "}";
}

string HandleCandles(string query)
{
   string symbol    = QueryParam(query, "symbol");
   string tfStr     = QueryParam(query, "timeframe");
   int    count     = (int)StringToInteger(QueryParam(query, "count"));

   if(symbol == "")   symbol = "XAUUSD";
   if(tfStr  == "")   tfStr  = "M5";
   if(count  <= 0)    count  = 500;
   if(count  > 5000)  count  = 5000;

   ENUM_TIMEFRAMES tf = StrToTF(tfStr);
   if(tf == WRONG_VALUE)
      return "{\"error\":\"unknown timeframe\"}";

   MqlRates rates[];
   // start=1 skips the current open bar, returns only closed candles
   int copied = CopyRates(symbol, tf, 1, count, rates);
   if(copied <= 0)
      return "[]";

   string items[];
   ArrayResize(items, copied);
   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);

   for(int i = 0; i < copied; i++)
   {
      string ts = FormatTime(rates[i].time);
      items[i] = "{" +
         JStr("symbol",    symbol)                   + "," +
         JStr("timeframe", tfStr)                    + "," +
         JStr("timestamp", ts)                       + "," +
         JDbl("open",      rates[i].open,  digits)   + "," +
         JDbl("high",      rates[i].high,  digits)   + "," +
         JDbl("low",       rates[i].low,   digits)   + "," +
         JDbl("close",     rates[i].close, digits)   + "," +
         JInt("volume",    rates[i].tick_volume)      + "," +
         JInt("spread",    rates[i].spread)           +
      "}";
   }
   return JArray(items);
}

string HandlePositions()
{
   int total = PositionsTotal();
   string items[];
   int found = 0;
   ArrayResize(items, total);

   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;

      string sym   = PositionGetString(POSITION_SYMBOL);
      int    ptype = (int)PositionGetInteger(POSITION_TYPE);
      double lots  = PositionGetDouble(POSITION_VOLUME);
      double open  = PositionGetDouble(POSITION_PRICE_OPEN);
      double cur   = PositionGetDouble(POSITION_PRICE_CURRENT);
      double sl    = PositionGetDouble(POSITION_SL);
      double tp    = PositionGetDouble(POSITION_TP);
      double prof  = PositionGetDouble(POSITION_PROFIT);
      double swap  = PositionGetDouble(POSITION_SWAP);
      double comm  = 0; // POSITION_COMMISSION removed in newer MT5 builds
      long   openT = PositionGetInteger(POSITION_TIME);
      string comm_s= PositionGetString(POSITION_COMMENT);
      long   magic = PositionGetInteger(POSITION_MAGIC);
      int    digits= (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);

      items[found++] = "{" +
         JInt("ticket",        (long)ticket)         + "," +
         JStr("symbol",        sym)                  + "," +
         JStr("direction",     ptype == 0 ? "BUY" : "SELL") + "," +
         JDbl("lots",          lots,  2)             + "," +
         JDbl("open_price",    open,  digits)        + "," +
         JDbl("current_price", cur,   digits)        + "," +
         JDbl("sl",            sl,    digits)        + "," +
         JDbl("tp",            tp,    digits)        + "," +
         JDbl("profit",        prof,  2)             + "," +
         JDbl("swap",          swap,  2)             + "," +
         JDbl("commission",    comm,  2)             + "," +
         JStr("opened_at",     FormatTime(openT))    + "," +
         JStr("comment",       comm_s)               + "," +
         JInt("magic",         magic)                +
      "}";
   }
   ArrayResize(items, found);
   return JArray(items);
}

string HandleOrder(const string &body)
{
   string symbol    = JsonField(body, "symbol");
   string direction = JsonField(body, "direction");
   double lots      = StringToDouble(JsonField(body, "lots"));
   double sl        = StringToDouble(JsonField(body, "sl"));
   double tp        = StringToDouble(JsonField(body, "tp"));
   string comment   = JsonField(body, "comment");

   if(symbol == "" || direction == "" || lots <= 0)
      return "{\"error\":\"invalid request body\"}";
   if(comment == "") comment = "mt5-bridge";
   if(StringLen(comment) > 31) comment = StringSubstr(comment, 0, 31);

   bool isBuy = (direction == "BUY");
   ENUM_ORDER_TYPE otype = isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;

   MqlTick tick;
   if(!SymbolInfoTick(symbol, tick))
      return "{\"error\":\"cannot get tick for " + symbol + "\"}";

   double price = isBuy ? tick.ask : tick.bid;
   string now   = FormatTime((long)TimeCurrent());

   MqlTradeRequest req  = {};
   MqlTradeResult  res  = {};
   req.action       = TRADE_ACTION_DEAL;
   req.symbol       = symbol;
   req.volume       = lots;
   req.type         = otype;
   req.price        = price;
   req.sl           = sl;
   req.tp           = tp;
   req.deviation    = Deviation;
   req.magic        = Magic;
   req.comment      = comment;
   req.type_time    = ORDER_TIME_GTC;
   req.type_filling = ORDER_FILLING_IOC;

   bool ok = OrderSend(req, res);
   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);

   if(ok && res.retcode == TRADE_RETCODE_DONE)
   {
      return "{" +
         JInt("ticket",    (long)res.order)    + "," +
         JStr("symbol",    symbol)             + "," +
         JStr("direction", direction)          + "," +
         JDbl("lots",      lots,         2)    + "," +
         JDbl("fill_price",res.price,    digits) + "," +
         JDbl("sl_price",  sl,           digits) + "," +
         JDbl("tp_price",  tp,           digits) + "," +
         JStr("filled_at", now)                + "," +
         JStr("comment",   comment)            + "," +
         "\"error\":null"                      +
      "}";
   }

   string errMsg = res.comment != "" ? res.comment : IntegerToString(GetLastError());
   return "{" +
      "\"ticket\":-1,"                           +
      JStr("symbol",    symbol)          + ","   +
      JStr("direction", direction)       + ","   +
      JDbl("lots",      lots, 2)         + ","   +
      "\"fill_price\":0.0,"                      +
      JDbl("sl_price",  sl, digits)     + ","   +
      JDbl("tp_price",  tp, digits)     + ","   +
      JStr("filled_at", now)             + ","   +
      JStr("comment",   comment)         + ","   +
      JStr("error",     "RETCODE_" + IntegerToString(res.retcode) + ": " + errMsg) +
   "}";
}

string HandleClose(long ticket)
{
   if(!PositionSelectByTicket((ulong)ticket))
      return "{\"error\":\"position " + IntegerToString(ticket) + " not found\"}";

   string sym   = PositionGetString(POSITION_SYMBOL);
   int    ptype = (int)PositionGetInteger(POSITION_TYPE);
   double lots  = PositionGetDouble(POSITION_VOLUME);
   double prof  = PositionGetDouble(POSITION_PROFIT);
   int    digits= (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);

   ENUM_ORDER_TYPE closeType = (ptype == 0) ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;

   MqlTick tick;
   if(!SymbolInfoTick(sym, tick))
      return "{\"error\":\"cannot get tick for " + sym + "\"}";

   double price = (ptype == 0) ? tick.bid : tick.ask;

   MqlTradeRequest req = {};
   MqlTradeResult  res = {};
   req.action       = TRADE_ACTION_DEAL;
   req.symbol       = sym;
   req.volume       = lots;
   req.type         = closeType;
   req.position     = (ulong)ticket;
   req.price        = price;
   req.deviation    = Deviation;
   req.magic        = Magic;
   req.comment      = "bridge-close";
   req.type_time    = ORDER_TIME_GTC;
   req.type_filling = ORDER_FILLING_IOC;

   bool ok = OrderSend(req, res);

   if(ok && res.retcode == TRADE_RETCODE_DONE)
   {
      return "{" +
         JInt("ticket",      ticket)              + "," +
         "\"closed\":true,"                           +
         JDbl("close_price", res.price, digits)   + "," +
         JDbl("profit",      prof,      2)         + "," +
         "\"error\":null"                              +
      "}";
   }

   string errMsg = res.comment != "" ? res.comment : IntegerToString(GetLastError());
   return "{" +
      JInt("ticket",  ticket)   + ","  +
      "\"closed\":false,"               +
      "\"close_price\":0.0,"            +
      "\"profit\":0.0,"                 +
      JStr("error", "RETCODE_" + IntegerToString(res.retcode) + ": " + errMsg) +
   "}";
}

// ─────────────────────────────────────────────────────────────────────────────
// HTTP response builder
// ─────────────────────────────────────────────────────────────────────────────

string HttpResponse(int code, string body)
{
   string status = code == 200 ? "200 OK" :
                   code == 404 ? "404 Not Found" : "400 Bad Request";
   return "HTTP/1.1 " + status + CRLF +
          "Content-Type: application/json" + CRLF +
          "Content-Length: " + IntegerToString(StringLen(body)) + CRLF +
          "Connection: close" + CRLF +
          CRLF +
          body;
}

// ─────────────────────────────────────────────────────────────────────────────
// JSON helpers
// ─────────────────────────────────────────────────────────────────────────────

string JStr(string key, string val)
{
   // Escape backslash and double-quote inside val
   StringReplace(val, "\\", "\\\\");
   StringReplace(val, "\"", "\\\"");
   return "\"" + key + "\":\"" + val + "\"";
}

string JDbl(string key, double val, int prec)
{
   return "\"" + key + "\":" + DoubleToString(val, prec);
}

string JInt(string key, long val)
{
   return "\"" + key + "\":" + IntegerToString(val);
}

string JBool(string key, bool val)
{
   return "\"" + key + "\":" + (val ? "true" : "false");
}

string JArray(string &items[])
{
   int n = ArraySize(items);
   if(n == 0) return "[]";
   string out = "[";
   for(int i = 0; i < n; i++)
   {
      if(i > 0) out += ",";
      out += items[i];
   }
   return out + "]";
}

// ─────────────────────────────────────────────────────────────────────────────
// Utilities
// ─────────────────────────────────────────────────────────────────────────────

ENUM_TIMEFRAMES StrToTF(const string &s)
{
   if(s == "M1")  return PERIOD_M1;
   if(s == "M5")  return PERIOD_M5;
   if(s == "M15") return PERIOD_M15;
   if(s == "M30") return PERIOD_M30;
   if(s == "H1")  return PERIOD_H1;
   if(s == "H4")  return PERIOD_H4;
   if(s == "D1")  return PERIOD_D1;
   return WRONG_VALUE;
}

string QueryParam(string query, string key)
{
   string search = key + "=";
   int pos = StringFind(query, search);
   if(pos < 0) return "";
   int start = pos + StringLen(search);
   int amp   = StringFind(query, "&", start);
   if(amp < 0) return StringSubstr(query, start);
   return StringSubstr(query, start, amp - start);
}

// Minimal JSON field extractor: finds "key":"value" or "key":number
string JsonField(string json, string key)
{
   string search = "\"" + key + "\"";
   int pos = StringFind(json, search);
   if(pos < 0) return "";

   int colon = StringFind(json, ":", pos + StringLen(search));
   if(colon < 0) return "";

   int valStart = colon + 1;
   // skip whitespace
   while(valStart < StringLen(json) &&
         (StringGetCharacter(json, valStart) == ' ' ||
          StringGetCharacter(json, valStart) == '\t'))
      valStart++;

   if(StringGetCharacter(json, valStart) == '"')
   {
      // string value
      valStart++;
      int end = valStart;
      while(end < StringLen(json))
      {
         ushort c = StringGetCharacter(json, end);
         if(c == '"' && (end == 0 || StringGetCharacter(json, end-1) != '\\'))
            break;
         end++;
      }
      return StringSubstr(json, valStart, end - valStart);
   }
   else
   {
      // numeric value — read until comma, } or whitespace
      int end = valStart;
      while(end < StringLen(json))
      {
         ushort c = StringGetCharacter(json, end);
         if(c == ',' || c == '}' || c == ' ' || c == '\r' || c == '\n')
            break;
         end++;
      }
      return StringSubstr(json, valStart, end - valStart);
   }
}

// Returns ISO-8601 UTC timestamp string from MT5 datetime (server time is broker local;
// we label it UTC — broker must be set to UTC for accurate timestamps)
string FormatTime(long t)
{
   MqlDateTime dt;
   TimeToStruct((datetime)t, dt);
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02dZ",
                       dt.year, dt.mon, dt.day,
                       dt.hour, dt.min, dt.sec);
}
//+------------------------------------------------------------------+
