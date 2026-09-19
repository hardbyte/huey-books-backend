from fastapi import Query


class PaginatedQueryParams:
    def __init__(
        self,
        skip: int = Query(0, ge=0, description="Skip this many items"),
        limit: int = Query(
            100, ge=1, le=2000, description="Maximum number of items to return"
        ),
    ):
        self.skip = skip
        self.limit = limit

    def __repr__(self):
        return f"<Pagination skip={self.skip}, limit={self.limit}>"

    def to_dict(self):
        return {"skip": self.skip, "limit": self.limit}
