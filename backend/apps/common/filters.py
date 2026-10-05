from rest_framework.filters import SearchFilter


class QSearchFilter(SearchFilter):
    """Same as DRF's SearchFilter but the query parameter is ?q= instead of ?search="""

    search_param = "q"
