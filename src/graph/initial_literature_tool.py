"""Per-request initial-search adapter. Recovery keeps the original Tool port."""

from src.retrieval.literature_query import LiteratureQueryGenerator
from src.tools.literature import LiteratureInput


class InitialLiteratureTool:
    def __init__(self, tool, generator: LiteratureQueryGenerator, original: str, mode: str):
        self.tool, self.generator, self.original, self.mode = tool, generator, original, mode
        self.selection = None

    def execute(self, request: LiteratureInput):
        if self.selection is not None:
            raise ValueError("Only one initial literature query preparation is allowed")
        self.selection = self.generator.prepare(self.original, self.mode)
        return self.tool.execute(LiteratureInput(operation=request.operation, top_k=request.top_k,
                                                query=self.selection["dense_query"],
                                                bm25_query=self.selection["bm25_query"]))
