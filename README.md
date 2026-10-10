# Polarsen

![banniere-100(1)](https://github.com/user-attachments/assets/6d6be6ae-7483-460d-b29d-321afde94b21)

**Find anything in your group chats. Without losing privacy.**

Polarsen turns your Telegram conversations into a searchable knowledge base — even when replies are scattered across hundreds of messages. Bring your own AI key. Self-host or use the hosted bot.

- 🧵 **Works on real conversations.** Handles non-linear group chats — interleaved threads, replies to old messages, conversations that overlap. Most chat search assumes linear conversations and breaks; Polarsen doesn't.
- 🔑 **Bring your own AI key.** Use Mistral, OpenAI, Gemini, or Grok. Pay providers directly. No markup, no middleman.
- 🏠 **Self-host or use hosted.** Deploy on your own infrastructure, or use the managed Telegram bot. Your choice, your data.
- ⚖️ **Source-available, becomes Apache 2.0.** Licensed under [FSL-1.1-ALv2](LICENSE.md) today. Each release converts to Apache 2.0 on its second anniversary. Yours forever.

## Try it

- **Hosted bot (Telegram)**: [@PolarsenBot](https://t.me/PolarsenBot)
- **Landing page**: [polarsen.io](https://polarsen.io)
- **Privacy & data flow**: [polarsen.io/privacy](https://polarsen.io/privacy)

---

## Why I built this

I built Polarsen because of my own family group chat on Telegram. Lots of discussions, important information drowning in scroll, and Telegram's search couldn't find anything once a conversation got interleaved — when someone replied to a message from days ago and the thread interleaved with three other ongoing topics.

Messages are private. The source-available + self-host commitment isn't a marketing tactic — it's the only way I'd want to build something that touches private conversations. You should be able to choose where your data lives.

— Julien

---

## Quickstart

If [just](https://github.com/casey/just) is installed, you can use the following recipes:

```bash
just bootstrap    # Install deps, start db/s3, run migrations
just app          # Start API + bot
just app-watch    # Start API + bot in watch mode
just infra        # Start db + s3 only
just listeners    # Start all listeners
just test         # Run tests
just up           # Start everything in watch mode
just up-no-watch  # Start everything without watch
just lint         # Lint with ruff
just fmt          # Format with ruff
just check        # Type check with pyright
```

### Manual setup

#### Start the services

To start the services, simply run the following command:

```bash
./bin/start.sh
```

This will start the API, the Telegram bot, the PostgreSQL database, and the S3 (minio) service.

#### Running tests

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

#### Build the Docker image

```bash
docker buildx bake
```

#### Generate the API models

To regenerate `models.py` from the OpenAPI spec (with the API running on `http://localhost:5050`):

```bash
./bin/gen-api-model.sh http://localhost:5050/openapi.json
```

---

## License

Polarsen is licensed under the [Functional Source License, Version 1.1, with an Apache 2.0 future license (FSL-1.1-ALv2)](LICENSE.md) — the same license used by Sentry.

In plain English:

- **You can** use Polarsen for any purpose that isn't a competing commercial product, study and modify the code, run it on your own infrastructure, fork it for non-commercial research and education, and redistribute it under the same terms.
- **You can't** offer Polarsen — or substantially similar functionality — as a commercial product or service while it's still under FSL.
- **After 2 years**, each release automatically converts to **Apache 2.0**, at which point you can do whatever you want with that version.

If FSL doesn't work for your use case, [open a discussion](https://github.com/polarsen-io/polarsen/discussions) — happy to talk.

## Contributing

Issues and pull requests are welcome. For larger changes, please open an issue first to discuss what you'd like to change.

By contributing, you agree that your contributions will be licensed under the project's license.
