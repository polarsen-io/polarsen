# Polarsen



**Polarsen** is an AI assistant that helps you find answers inside your conversations.  
Forget endless scrolling — just ask, and Polarsen retrieves the right context and responds intelligently.   

## Why Polarsen?
- Tired of digging for that link, recipe, or file?  
- Want instant summaries of group discussions?  
- Need to ask natural questions about your past messages?  

Polarsen makes your conversations searchable and useful.  


🔎 **Search smarter** — semantic search across chats  
🤖 **AI answers** — powered by Retrieval-Augmented Generation (RAG)  
💬 **Cross-platform ready** — starting with Telegram, designed for more  
🔒 **Private by design** — your data stays yours, self-hostable, source-available.

---


## Quickstart

### Start the services

To start the services, simply run the following command:

```bash
./bin/start.sh
```

This will start the API, the Telegram bot, the PostgresSQL database, and the S3 (minio) service.


### Running tests  

You can run the full test suite with:

```bash
./bin/tests.sh
```

or for faster iteration, start the services first and then run the tests with:

```bash
# Run one time to setup the services
./bin/tests.sh
# Run each time you want to run the tests
uv run pytest
```  

### Build the Docker image

To build the Docker image, you can use the following command:

```bash
docker buildx bake
```


### Generate the API models

To generate the `models.py` file based on the openapi spec, you can use the following command
(once the API is running on `http://localhost:5050`):

```bash
./bin/gen-api-model.sh http://localhost:5050/openapi.json
```



