-- Rename legacy gx10_agent_id column to runtime_agent_id (Babo product naming).
ALTER TABLE "agents" RENAME COLUMN "gx10_agent_id" TO "runtime_agent_id";
