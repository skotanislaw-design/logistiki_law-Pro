import os
from typing import Optional, Any
from pathlib import Path
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, BeforeValidator, ConfigDict, AliasChoices
from typing import Annotated
from bson import ObjectId

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
db_name = os.environ.get('DB_NAME', 'nomos_one')

if mongo_url.startswith("mongomock://"):
    # In-memory database for unit tests / local demos (no mongod required).
    from mongomock_motor import AsyncMongoMockClient
    client = AsyncMongoMockClient()
else:
    client = AsyncIOMotorClient(mongo_url)
db = client[db_name]


async def fetch_all(cursor) -> list[dict]:
    """Όλα τα έγγραφα ενός cursor — **χωρίς όριο**.

    Το `cursor.to_list(n)` κόβει σιωπηλά στα πρώτα `n` έγγραφα, με τη σειρά που τύχει να έχει ο cursor. Όπου το
    άθροισμα ή ο έλεγχος πρέπει να καλύπτει *όλα* τα έγγραφα (ανείσπρακτα, ΦΠΑ, δήλωση περιόδου) αυτό δίνει λάθος
    αριθμό χωρίς κανένα σφάλμα — και συνήθως λείπουν ακριβώς τα παλαιότερα, δηλαδή τα ληξιπρόθεσμα. Εδώ η ανάγνωση
    γίνεται τμηματικά από τον driver, όσο μεγάλη κι αν είναι η συλλογή.

    Το `to_list(n)` μένει σωστό μόνο για ρητό «τα τελευταία n» (με `.sort()` και `.limit(n)` στον ίδιο cursor).
    """
    return [doc async for doc in cursor]


async def fetch_capped(cursor, limit: int) -> tuple[list[dict], bool]:
    """Τα πρώτα `limit` έγγραφα ενός cursor **και αν υπήρχαν κι άλλα** — για λίστες οθόνης, ποτέ για αθροίσματα.

    Το `to_list(limit)` κόβει σιωπηλά: ο χρήστης βλέπει 500 υποθέσεις και δεν μαθαίνει ότι υπάρχει και η 501η. Εδώ ζητείται
    ένα έγγραφο παραπάνω· το έξτρα δεν επιστρέφεται, δείχνει μόνο ότι κόπηκε κάτι, ώστε η απάντηση να το δηλώσει
    (`http_utils.disclose_truncation`). Η σειρά και το όριο ανήκουν στον καλούντα: ο cursor πρέπει να έχει `sort()`.
    """
    docs = [doc async for doc in cursor.limit(limit + 1)]
    return docs[:limit], len(docs) > limit


def validate_object_id(v: Any) -> str:
    if isinstance(v, ObjectId):
        return str(v)
    if isinstance(v, str) and v:
        return v
    raise ValueError("Invalid ObjectId")


PyObjectId = Annotated[str, BeforeValidator(validate_object_id)]


class BaseDocument(BaseModel):
    # `allow_inf_nan=False`: ούτε `NaN`/`Infinity` ούτε τα κείμενα "NaN", "inf", "1e999" γίνονται πεδίο αριθμού σε κανένα
    # έγγραφο της εφαρμογής. Ο φραγμός JSON (json_guard) βλέπει μόνο τις σταθερές του JSON· τα κείμενα τα μετατρέπει η Pydantic
    # σε πραγματικό NaN, και ένα NaN που αποθηκεύεται χαλά κάθε άθροισμα και κάθε απάντηση JSON του γραφείου.
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True, allow_inf_nan=False)
    id: Optional[PyObjectId] = Field(default=None, validation_alias=AliasChoices("_id", "id"))

    @classmethod
    def from_mongo(cls, doc: Optional[dict]):
        if doc is None:
            return None
        doc = dict(doc)
        if "_id" in doc:
            doc["id"] = str(doc.pop("_id"))
        return cls(**doc)

    def to_mongo(self) -> dict:
        data = self.model_dump(exclude_none=True)
        _id = data.pop("id", None)
        if _id is not None and ObjectId.is_valid(_id):
            data["_id"] = ObjectId(_id)
        return data
