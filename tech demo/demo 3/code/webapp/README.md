# TSS2 Dashboard (demo 3)

A small dashboard for TSS2 metadata endpoints: the `.ttl` files in `demo 3/metadata endpoint/`, plus any online endpoint you add.
Pick a data source in the drop down, then use the tabs:

| Tab | What it does |
|---|---|
| Overview | What the endpoint holds: the `csvw:Table`, the snippet template and every snippet, plus the raw Turtle |
| Series inspector | Pick one series (snippet) and see everything known about it. Every value is marked: stated on the snippet itself (own), inherited from its group's template (from group), or replacing the template's value (overrides group). Below: the Turtle, CSVW JSON and first CSV rows, with the parts this series uses marked |
| Files | The CSV the endpoint points to with `csvw:url` (first 20 lines) and the CSVW file it points to with `wdrs:describedby`. Each has a download icon for the whole file |
| CSVW validation | Runs the `csvw` Python validator on the CSV and its CSVW file |
| Query | A form to get the readings of one time series between two times |

## Running it

You need two terminals. Demo 3 uses its own ports (8001 and 5174), so it can run next to demo 2 (8000 and 5173).
The `cd` commands below start from the `tech demo` folder.

**Backend** (FastAPI, port 8001)

```
cd "demo 3/code/webapp/backend"
pip install -r requirements.txt
uvicorn main:app --reload --port 8001
```

Test it in the browser: <http://localhost:8001/endpoints>, or look at all routes at <http://localhost:8001/docs>.

**Frontend** (React + Vite, port 5174)

```
cd "demo 3/code/webapp/frontend"
npm install
npm run dev
```

Then open <http://localhost:5174>.

If you see "Failed to fetch" / "could not reach the backend", check that:
- the backend terminal shows `Uvicorn running on http://127.0.0.1:8001`
- the browser address is exactly `http://localhost:5174`. The backend only answers pages from that address (CORS).
- you started the backend before opening the page. If not, refresh the page.

## Online endpoints

Under the drop down, paste the web address of a TSS2 metadata endpoint (a Turtle file, for example
`https://example.org/metadata endpoint/my_series_tss2.ttl`) and click **Add**. The backend downloads it and checks
that it holds a `TSS2:SnippetGroup` with a `csvw:Table`. If so, it appears in the drop down as "(online)" and every tab
works as for the local files:

- relative addresses in it, like `<../data/x.csv>`, are read against the endpoint's own web address,
  so `csvw:url` and `wdrs:describedby` point at files on the same website;
- the backend downloads the CSVW file and the CSV from there (a big CSV is read line by line, never all at once).

The online endpoints you add are remembered in `backend/online_endpoints.json`. **Remove** (next to the drop down) takes the
picked online endpoint out of the list again.

If the backend runs into an error, the page shows the error message itself (it starts with "The backend crashed" or names the file it could not read).

## How the code is organised

### Backend: `backend/`

**`tss2.py`** holds the TSS2 inheritance rule, in one place: a snippet uses the value from its group's template, unless it states its own value. `resolve_with_source` applies the rule to one property and says where the value came from: `own`, `group`, `override` or `missing`.

**`main.py`** holds everything else, in this order:

1. **SPARQL queries.** `TABLE_QUERY` gets the table and the snippet template. `SNIPPETS_QUERY` gets one row per snippet.
2. **Files: local or online.** A file "location" is a path inside demo 3 (`data/x.csv`) or a web address. `file_exists`, `read_text`, `read_lines` and `send_as_download` work for both, so the routes don't have to care which one it is.
3. **Metadata endpoints.** The local `.ttl` files plus the online ones from `online_endpoints.json`. `load_graph` reads one endpoint's Turtle, wherever it is.
4. **Helper functions.** `get_endpoint_info` runs both queries, applies the rule from `tss2.py` to every snippet, and puts the answers in one dictionary. Most routes use it.
5. **Helper functions for the series inspector.** `describe` turns an RDF value into readable text (plus the exact RDF for hovering). `mark_turtle` and `mark_csvw` mark the lines of the source files.
6. **Routes**:

| Route | Returns |
|---|---|
| `GET /endpoints` | the list for the drop down |
| `POST /endpoints/online?url=...` | adds an online endpoint (after checking it) |
| `DELETE /endpoints/{id}` | removes an online endpoint |
| `GET /endpoints/{id}` | everything about one endpoint (used by the Overview and Query tabs) |
| `GET /endpoints/{id}/series/metadata?snippet=...` | the series inspector: every value of one snippet with its source (own / group / override), the group, and the marked source files |
| `GET /endpoints/{id}/csv` | the first lines of the CSV file |
| `GET /endpoints/{id}/csv/download` | the whole CSV file as a download |
| `GET /endpoints/{id}/csvw` | the CSVW JSON file |
| `GET /endpoints/{id}/csvw/download` | the CSVW JSON file as a download |
| `GET /endpoints/{id}/validate` | the result of the CSVW validation |
| `GET /endpoints/{id}/query?snippet=...&start=...&end=...&limit=...` | the readings |

The `{id}` of a local endpoint is its file name without `.ttl`, for example `waterinfo_289423042_tss2`. Online endpoints get `online-1`, `online-2`, ...
`snippet=...` is the full IRI of a snippet, for example `https://example.org/ex#snippet-grootdok-ec20`.

The query route shows how TSS2 and CSVW work together:

- TSS2 says which CSV to read (`csvw:url` of the table), which columns to use (`TSS2:timeColumn`, `TSS2:valueColumn`) and which time window the series covers (`tss:from`, `tss:until`). Each of these can come from the group's template.
- Those columns are CSVW column *names*. The CSVW file says which CSV header belongs to each name (`timestamp` → `Timestamp`) and which delimiter the CSV uses.

### Frontend: `frontend/src/`

| File | What it is |
|---|---|
| `config.js` | the backend address (`http://localhost:8001`), and two helpers that turn errors into readable text |
| `App.jsx` | the drop down and the tabs. It loads the endpoint list and the picked endpoint, and passes `info` to the tabs |
| `OnlineEndpointForm.jsx` | the "Add an online endpoint" form under the drop down |
| `Overview.jsx` | the Overview tab. It only shows the `info` it gets from App |
| `Inspector.jsx` | the Series inspector tab: the series picker, the panel with own / from group / overrides markers, and the group's panel |
| `SourceFiles.jsx` | the Turtle / CSVW JSON / CSV rows views under the inspector, with the used parts coloured |
| `FileViewer.jsx` | shows one file, with a download icon. App uses it twice: for the CSV and for the CSVW file |
| `Validation.jsx` | the validation button and its result |
| `QueryForm.jsx` | the query form and the results table |
| `App.css` | all the styling |

Every component talks to the backend the same way:

```js
fetch(url)
  .then((response) => response.json())
  .then((data) => {
    if (data.detail) setError(errorText(data.detail)) // FastAPI puts its error message in "detail"
    else setResult(data)
  })
  .catch((err) => setError(fetchErrorText(err)))      // the backend could not be reached at all
```

App and Inspector add one more thing: an `ignore` flag, so that when you switch quickly, a late answer for your previous choice is thrown away instead of being shown.
