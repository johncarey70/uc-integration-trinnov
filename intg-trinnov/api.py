"""
Integration API Module.

This module initializes the Integration API using the ucapi library and sets up an 
Asyncio event loop. It provides a foundation for interacting with the API.

Attributes:
    loop (asyncio.BaseEventLoop): The Asyncio event loop used by the API.
    api (ucapi.IntegrationAPI): The initialized Integration API instance.
"""

import asyncio

import ucapi

loop = asyncio.new_event_loop()
api = ucapi.IntegrationAPI(loop)
