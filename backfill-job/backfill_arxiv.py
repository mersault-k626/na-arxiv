import urllib.parse, urllib.request
import xml.etree.ElementTree as ET
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pyarrow.fs as fs
import time

BACKFILL_RANGE = ('2024-01-01', '2026-07-30')
# assign quarter
quarters = pd.period_range(start=BACKFILL_RANGE[0], end=BACKFILL_RANGE[1], freq='Q')

BASE_URL = "http://export.arxiv.org/api/query?"
HEADERS = {"User-Agent": "MyArxivClient/1.0 (martyshort52@gmail.com)"}
PAGE_SIZE = 100

ns = {
    'atom': 'http://www.w3.org/2005/Atom',
    'opensearch': 'http://a9.com/-/spec/opensearch/1.1/',
}

# func to fetch output from query run
def fetch_page(q, start, retries=10):
    params = {'search_query': q, 'start': start, 'max_results': PAGE_SIZE}
    url = BASE_URL + urllib.parse.urlencode(params)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req) as resp:
                return ET.fromstring(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and attempt < retries - 1:
                wait = int(e.headers.get('Retry-After', min(60, 15 * (attempt + 1))))
                time.sleep(wait)
                continue
            raise

# empty list for rows
entries = []

# stick 
cutoff = pd.Timestamp(BACKFILL_RANGE[1])

# loop over quarter
for quarter in quarters:
    q_start = quarter.start_time.strftime('%Y%m%d%H%M')
    q_end_ts = min(quarter.end_time, cutoff)
    q_end = q_end_ts.strftime('%Y%m%d%H%M')
    q = f'all:"information retrieval" AND submittedDate:[{q_start} TO {q_end}]'

    # fetch per quarter
    root = fetch_page(q, 0)
    total_results = int(root.find('opensearch:totalResults', ns).text)
    quarter_entries = root.findall('atom:entry', ns)

    start = PAGE_SIZE
    while start < total_results:
        time.sleep(3)
        page_root = fetch_page(q, start)
        quarter_entries.extend(page_root.findall('atom:entry', ns))
        start += PAGE_SIZE

    # print log
    print(f"{quarter}: {len(quarter_entries)}/{total_results} entries")
    entries.extend(quarter_entries)
    time.sleep(5)

# create empty list
rows = []

# loop over each element in entries list
for entry in entries:
    # fetch author, category, pdf link
    author_names = [a.find('atom:name', ns).text for a in entry.findall('atom:author', ns)]
    category_terms = [c.attrib['term'] for c in entry.findall('atom:category', ns)]
    pdf_link_el = entry.find("atom:link[@title='pdf']", ns)

    # create a dict for each row, with col names as key, chuck all inside rows[]
    rows.append({
        'id': entry.find('atom:id', ns).text.strip(),
        'title': entry.find('atom:title', ns).text.strip().replace('\n', ' '),
        'author': '; '.join(author_names),
        'category': '; '.join(category_terms),
        'abstract': entry.find('atom:summary', ns).text.strip().replace('\n', ' '),
        'published': entry.find('atom:published', ns).text.strip(),
        'url': pdf_link_el.attrib['href'] if pdf_link_el is not None else None,
    })

# convert list of dict into df
df = pd.DataFrame(rows)
# conv time to match pa
df['published'] = pd.to_datetime(df['published'], format='%Y-%m-%dT%H:%M:%SZ')
# sort by published date
df = df.sort_values('published').reset_index(drop=True)

# define s3 location
s3_filesystem = fs.S3FileSystem(region="ap-southeast-1")
s3_path = "arvix-db/raw-arxiv/raw-arvix-entries.parquet"

# create/append latest df into existing parquet
existing_file_info = s3_filesystem.get_file_info(s3_path)
if existing_file_info.type != fs.FileType.NotFound:
    existing_df = pq.read_table(s3_path, filesystem=s3_filesystem).to_pandas()
    df = pd.concat([existing_df, df], ignore_index=True)
    df = df.drop_duplicates(subset='id').sort_values('published').reset_index(drop=True)

parquet_schema = pa.schema([
    ('id', pa.string()),
    ('title', pa.string()),
    ('author', pa.string()),
    ('category', pa.string()),
    ('abstract', pa.string()),
    ('published', pa.timestamp('ms')),
    ('url', pa.string()),
])

table = pa.Table.from_pandas(df, schema=parquet_schema, preserve_index=False)

pq.write_table(table, s3_path, filesystem=s3_filesystem)

file_info = s3_filesystem.get_file_info(s3_path)
if file_info.type != fs.FileType.NotFound:
    print(f"Backfill completed. raw-arvix-entries.parquet has been uploaded to s3://{s3_path}")
else:
    print(f"Backfill failed. raw-arvix-entries.parquet not found at s3://{s3_path}")