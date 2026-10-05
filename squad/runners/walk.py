"""The child walk of a container, once: loop and each both read the reserved `next` words here (ADR-0100)."""


def next_word(ctx, node, res):
    return res.word or ctx.route(node, res.outcome)


async def walk_children(ctx, node, sc):
    """Run the children in order. Return `(child, word, res)` for the first child whose word is not `continue`, else `(None, "continue", None)`."""
    for child in node["children"]:
        res = await ctx.exec_node(child, sc)
        word = next_word(ctx, ctx.nodes[child], res)
        if word != "continue":
            return child, word, res
    return None, "continue", None
