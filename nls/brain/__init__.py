"""NLS Brain — Cognitive Subsystems.

Biological/cognitive systems: ANS, hormones, memory, drives, DMN, signals.

Sub-modules:
    autonomic        – ANS state machine (AutonomicNervousSystem)
    hypothalamus     – Pharmacokinetic hormones (HypothalamusEngine)
    working_memory   – Slot-based WM + DualWM (DualWorkingMemory)
    dmn              – Default Mode Network (DefaultModeNetwork)
    drives           – Autonomous drive engine (DriveEngine)
    agency           – Proactive agency engine (AgencyEngine)
    self_state       – Unified self-representation (SelfState)
    ofc              – Orbitofrontal cortex (OrbitofrontalCortex)
    predictive       – Prediction engine (PredictiveProcessor)
    network_dynamics – ECN/SN/DMN three-network (NetworkDynamics)
    thinking         – Thinking mode control
    crystallization  – Skill crystallization (CrystallizationEngine)
    signal_probes    – Probe classifiers
    circadian        – Sleep/wake schedule (CircadianSchedule)
    logger           – JSONL structured logger (EventLogger)
    event_logger     – Event logger factory
    brain_context    – Brain context builder
    dream_findings   – Dream data structures (DreamFinding)
"""
