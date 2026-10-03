from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue
from app.core.config import settings
client = QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY)
for st in ['statutes', 'judgement', 'judgements']:
    r = client.count(collection_name=settings.QDRANT_COLLECTION, count_filter=Filter(must=[FieldCondition(key='source_type', match=MatchValue(value=st))]), exact=True)
    print(f'{st}: {r.count}')