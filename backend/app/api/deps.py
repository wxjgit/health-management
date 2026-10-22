from fastapi import Request


def get_engine(request: Request):
    return request.app.state.engine
