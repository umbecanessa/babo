# Babo composition recipes

Recipe files that teach agents how to compose the 4 core tools
(read, write, edit, bash) into multi-step workflows.

## Structure

Each recipe is a JSON file with:

- `name` -- Unique identifier
- `description` -- What the recipe accomplishes
- `category` -- communication, devops, data, system
- `difficulty` -- basic, intermediate, advanced
- `prerequisites` -- What needs to be available
- `steps` -- Ordered list of tool calls with examples
- `learning_signals` -- Key facts to store in DomainDB

## Categories

- `communication/` -- Telegram, Slack, Discord, email
- `devops/` -- GitHub, CI/CD, Docker, deployment
- `data/` -- Web scraping, CSV/JSON processing, API integration
- `system/` -- Cron, process management, service monitoring

## Adding Recipes

Create a new `.json` file in the appropriate category directory.
Follow the schema of existing recipes. The recipe onboarding
system will automatically discover and teach new recipes.
