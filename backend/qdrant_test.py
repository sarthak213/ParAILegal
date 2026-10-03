from qdrant_client import QdrantClient
from qdrant_client.models import PayloadSchemaType
from app.core.config import settings

client = QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY)

# Delete the existing payload index and recreate it
# This forces Qdrant to index ALL existing payloads
print('Deleting old payload index...')
try:
    client.delete_payload_index(
        collection_name=settings.QDRANT_COLLECTION,
        field_name='source_type',
    )
    print('  Deleted.')
except Exception as e:
    print(f'  Could not delete: {e}')

print('Recreating payload index...')
client.create_payload_index(
    collection_name=settings.QDRANT_COLLECTION,
    field_name='source_type',
    field_schema='keyword',
    wait=True,
)
print('  Done.')

# Verify
from qdrant_client.models import Filter, FieldCondition, MatchValue
for st in ['constitution', 'bns', 'bnss', 'bsa', 'judgement']:
    r = client.count(
        collection_name=settings.QDRANT_COLLECTION,
        count_filter=Filter(must=[FieldCondition(key='source_type', match=MatchValue(value=st))]),
        exact=True
    )
    print(f'  {st}: {r.count}')