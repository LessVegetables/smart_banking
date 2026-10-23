"""Private document storage. Public files are never mounted as static assets."""
import os
from .db import DATA

class LocalStorage:
    def __init__(self):self.root=DATA/'documents';self.root.mkdir(exist_ok=True)
    def put(self,key,content,media_type):(self.root/key).write_bytes(content)
    def get(self,key):return (self.root/key).read_bytes()

class S3Storage:
    def __init__(self):
        import boto3
        self.client=boto3.client('s3',endpoint_url=os.environ.get('S3_ENDPOINT_URL') or None,region_name=os.environ.get('AWS_DEFAULT_REGION','us-east-1'))
        self.bucket=os.environ['S3_BUCKET']
    def put(self,key,content,media_type):self.client.put_object(Bucket=self.bucket,Key='documents/'+key,Body=content,ContentType=media_type)
    def get(self,key):return self.client.get_object(Bucket=self.bucket,Key='documents/'+key)['Body'].read()

_store=None
def storage():
    global _store
    if _store is None:_store=S3Storage() if os.environ.get('S3_BUCKET') else LocalStorage()
    return _store
