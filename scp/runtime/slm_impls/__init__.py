"""
[Task 9-B] SLM implementations package — extracted from runtime/slms.py.

TẠI SAO: slms.py 4,052 LOC god file với 56 SLM classes. Tách major SLMs
(science, humanities, misc) vào package này. Backward-compatible — slms.py
re-exports all SLMs (public API unchanged).

Modules:
  sciences_slm.py   — MathSLM, BiologySLM, ChemistrySLM, RealitySLM, AstronomySLM
  humanities_slm.py — GeographySLM, HistorySLM
  misc_slm.py       — ConversionSLM, EntertainmentSLM, UniversalSLM

Smaller SLMs (WeatherSLM, LogicSLM, StatisticsSLM, GeneralSLM, ReligionSLM,
FoodSLM, CitySLM, HolidaySLM, AnimalFactsSLM, AdviceSLM, ChuckNorrisSLM,
_DomainSLM subclasses) remain in slms.py (small enough, lower risk).
"""
