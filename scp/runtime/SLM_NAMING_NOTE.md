# SLM Naming Note

In SCP, "SLM" stands for **"Specialized Logic Module"** (NOT "Small Language Model").

SCP's SLMs are **deterministic dispatchers** that route questions to DataSources
and verify answers — they are NOT neural network models.

The name "SLM" was chosen early in development and is kept for backward
compatibility (300+ imports reference it). To avoid confusion:

- When reading SCP code, mentally substitute "SLM" → "DomainExpert"
- New code should use the alias `DomainExpert` (available in slm_base.py)
- The classes `MathSLM`, `BiologySLM`, etc. are equivalent to `MathExpert`,
  `BiologyExpert`, etc.

## Aliases (backward compat):

```python
from scp.runtime.slm_base import BaseSLM as DomainExpert  # alias
# All SLM classes can be imported with either name:
# from scp.runtime.slms import MathSLM as MathExpert
```

## Why not rename?

Renaming 33 SLM classes across 6 implementation files + slms.py re-exports
would touch ~50 files and risk breaking imports. The alias approach achieves
clarity without the risk.
