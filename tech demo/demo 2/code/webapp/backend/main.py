from fastapi import FastAPI
from Timeseries import Series
from rdflib import Graph
from csvw import CSVW
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname
from fastapi.middleware.cors import CORSMiddleware


METADATA_DIR = Path(__file__).resolve().parents[3] / "metadata endpoint"

tss2Query = """
PREFIX rdfs:    <http://www.w3.org/2000/01/rdf-schema#>
PREFIX dcterms: <http://purl.org/dc/terms/>
PREFIX TSS2:    <https://example.org/tss2#>
PREFIX tss: <https://w3id.org/tss#>

SELECT ?snippet ?label ?tsId ?fromdate ?untildate
WHERE {
  ?snippet a TSS2:Snippet ;
           rdfs:label ?label ;
           dcterms:identifier ?tsId .

  ?group TSS2:hasSnippet ?snippet ;
         TSS2:snippetTemplate ?template .
  ?template tss:from ?fromdate ;
            tss:until ?untildate .
}
"""

CSVWQuery = """
PREFIX csvw: <http://www.w3.org/ns/csvw#>
PREFIX ex: <https://example.org/ex#>
PREFIX wdrs: <http://www.w3.org/2007/05/powder-s#>

SELECT ?table ?CSVFile
WHERE {
    ?subject csvw:url ?CSVFile.
    ?subject wdrs:describedby  ?table.
}
"""

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def hello():
    return {"message": "Hello from the backend"}


@app.get("/series")
def get_series():
    graph = Graph()
    graph.parse(METADATA_DIR /"waterinfo_289423042_tss2.ttl", format="turtle")
    graph.parse(METADATA_DIR /"waterinfo_289429042_tss2.ttl", format="turtle")
    graph.parse(METADATA_DIR /"waterinfo_289435042_tss2.ttl", format="turtle")
    graph.parse(METADATA_DIR /"waterinfo_289441042_tss2.ttl", format="turtle")

    results = []
    for row in graph.query(tss2Query):
        results.append({
            "id": str(row.snippet),
            "label": str(row.label),
            "tsId": str(row.tsId),
            "from": str(row.fromdate),
            "until": str(row.untildate)
        })
    return results


@app.get("/CSVW")
def get_CSVW():
    graph = Graph()
    graph.parse(METADATA_DIR /"waterinfo_289423042_tss2.ttl", format="turtle")
    graph.parse(METADATA_DIR /"waterinfo_289429042_tss2.ttl", format="turtle")
    graph.parse(METADATA_DIR /"waterinfo_289435042_tss2.ttl", format="turtle")
    graph.parse(METADATA_DIR /"waterinfo_289441042_tss2.ttl", format="turtle")

    locations = []
    for row in graph.query(CSVWQuery):
        locations.append(
            str(row.table)
        )
        data = CSVW(Path(url2pathname(urlparse(str(row.table)).path)), validate=True)
        print("valid:", data.is_valid)
        for w in data.warnings:
            print("warning:", w.message)
        table = data.tables[0]
        print("CSV:", table.url)
        for col in table.tableSchema.columns:
            print(f"  {col.name:26} {col.datatype.base:9} header: {col.titles.getfirst()}")

    return locations


