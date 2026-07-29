"""Social graph — follow / unfollow between users.

Table `multitenant-follows`: PK=follower_id, SK=followee_id (one item per follow).
GSI `by_followee` (PK=followee_id) answers "who follows X" + follower counts.
Kept deliberately simple: no denormalized counters (compute via a COUNT query),
so there's nothing to drift. Add counters later if follower counts get hot.
"""

import os
import time

import boto3
from botocore.exceptions import ClientError


REGION = os.environ.get("AWS_REGION", "ap-south-1")
FOLLOWS_TABLE = os.environ.get("FOLLOWS_TABLE", "multitenant-follows")

_ddb = boto3.client("dynamodb", region_name=REGION)


def follow(follower_id: str, followee_id: str) -> bool:
    """Follow a user. Returns False if you tried to follow yourself."""
    if not follower_id or not followee_id or follower_id == followee_id:
        return False
    _ddb.put_item(TableName=FOLLOWS_TABLE, Item={
        "follower_id": {"S": follower_id},
        "followee_id": {"S": followee_id},
        "created_at": {"N": str(int(time.time()))},
    })
    return True


def unfollow(follower_id: str, followee_id: str) -> None:
    _ddb.delete_item(TableName=FOLLOWS_TABLE, Key={
        "follower_id": {"S": follower_id}, "followee_id": {"S": followee_id}})


def is_following(follower_id: str, followee_id: str) -> bool:
    try:
        r = _ddb.get_item(TableName=FOLLOWS_TABLE, Key={
            "follower_id": {"S": follower_id}, "followee_id": {"S": followee_id}})
        return "Item" in r
    except ClientError:
        return False


def list_following(follower_id: str) -> list[str]:
    """All followee_ids this user follows (paginated internally)."""
    ids: list[str] = []
    kw = {"TableName": FOLLOWS_TABLE,
          "KeyConditionExpression": "follower_id = :f",
          "ExpressionAttributeValues": {":f": {"S": follower_id}}}
    while True:
        r = _ddb.query(**kw)
        ids += [i["followee_id"]["S"] for i in r.get("Items", [])]
        lek = r.get("LastEvaluatedKey")
        if not lek:
            break
        kw["ExclusiveStartKey"] = lek
    return ids


def count_followers(followee_id: str) -> int:
    try:
        r = _ddb.query(TableName=FOLLOWS_TABLE, IndexName="by_followee",
                       KeyConditionExpression="followee_id = :f",
                       ExpressionAttributeValues={":f": {"S": followee_id}},
                       Select="COUNT")
        return r.get("Count", 0)
    except ClientError:
        return 0
