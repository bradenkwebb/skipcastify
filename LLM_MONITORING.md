# LLM Performance Monitoring

The `llm_monitor.py` module tracks and profiles LLM (Ollama) performance during ad detection.

## Features

- **Per-Call Metrics**: Times each LLM call, tracks success/failure, and counts returned ad spans
- **Resource Monitoring**: Captures CPU and memory usage during LLM calls (both system and Ollama process)
- **Episode Profiles**: Saves detailed JSON profiles for each episode with all call metrics and aggregate stats
- **Real-Time Logging**: Logs each call result immediately with timing and resource info
- **Summary Reports**: Prints a summary at the end of episode processing with aggregate statistics

## Output

### Real-Time Logs

As the LLM processes sections, you'll see logs like:

```
✓ Section 0 [0ms-600000ms]: 45.3s, 2 spans found, Memory: 2048MB, CPU: 85.2%
✓ Section 1 [570000ms-1170000ms]: 52.1s, 1 spans found, Memory: 2156MB, CPU: 78.5%
✗ Section 2 [1140000ms-1740000ms]: 30.0s, (timeout), Memory: 1920MB, CPU: 45.2%
```

### Episode Summary

After all sections are processed, a summary is printed:

```
====================================================================================================
LLM PERFORMANCE SUMMARY
====================================================================================================
Episode: episode-name
Total calls: 5 (4 success, 1 failed)
Total LLM time: 238.4s
Avg call time: 47.7s
Call time range: 30.0s - 52.1s
Total spans found: 7
Peak memory: 2156MB
Avg memory: 2042MB
====================================================================================================
```

### Saved Profiles

Each episode generates a JSON profile at `data/llm_profiles/<episode_name>_llm_profile.json`:

```json
{
  "episode_name": "philosophize-this-episode_237",
  "timestamp": "2025-12-07T19:30:15.123456",
  "call_count": 5,
  "calls": [
    {
      "section_index": 0,
      "section_start_ms": 0,
      "section_end_ms": 600000,
      "duration_sec": 45.3,
      "success": true,
      "spans_returned": 2,
      "peak_memory_mb": 2048.5,
      "avg_cpu_percent": 82.1,
      "ollama_peak_memory_mb": 1850.2
    },
    ...
  ],
  "stats": {
    "total_calls": 5,
    "successful_calls": 4,
    "failed_calls": 1,
    "total_llm_time_sec": 238.4,
    "avg_call_time_sec": 47.7,
    ...
  }
}
```

## Usage

The monitor is automatically integrated into the preview and main processing pipelines. No additional setup required.

To access monitoring data programmatically:

```python
from skipcastify.services.llm_monitor import get_monitor

monitor = get_monitor()
monitor.start_episode("my_episode_name")

# ... LLM processing happens ...

# Record a call
monitor.record_call(
    section_index=0,
    section_start_ms=0,
    section_end_ms=600000,
    duration_sec=45.3,
    success=True,
    spans_returned=2
)

# Print summary
monitor.print_episode_summary()

# Save profile
monitor.save_episode_profile()
```

## Timeout Configuration

If you're getting timeout errors with Ollama, increase the timeout in `llm_utils.py`:

```python
# In call_ollama_generate function
timeout = 300  # increased from default (adjust as needed)
```

## Analyzing Profiles

To analyze all episode profiles:

```bash
# List all profiles
ls -lh data/llm_profiles/

# View a specific profile
cat data/llm_profiles/<episode_name>_llm_profile.json | python -m json.tool
```

## Optimization Tips

Based on monitoring data, you can:

1. **Identify slow sections**: Sections with consistently high call times may have verbose transcripts
2. **Monitor memory**: If memory peaks are high, consider processing episodes in batches
3. **Tune the prompt**: If success rates are low, review the LLM prompt and adjust based on failure patterns
4. **Batch processing**: Use aggregate stats across episodes to decide on parallel processing strategy
