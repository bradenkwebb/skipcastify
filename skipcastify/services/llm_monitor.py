"""
Monitor and profile LLM performance and resource usage.

Tracks:
- Time per section LLM call
- Total LLM time per episode
- Token usage (if available)
- Resource utilization during calls
- Performance statistics and trends
"""

import time
import logging
import json
import os
import psutil
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class ProcessMetrics:
    """Resource usage snapshot for a process."""
    timestamp: str
    cpu_percent: float
    memory_mb: float
    memory_percent: float


@dataclass
class LLMCallMetrics:
    """Metrics for a single LLM call."""
    episode_name: str
    section_index: int
    section_start_ms: int
    section_end_ms: int
    duration_sec: float
    success: bool
    error_msg: Optional[str] = None
    spans_returned: int = 0
    
    # Resource metrics (sampled during call)
    peak_cpu_percent: float = 0.0
    peak_memory_mb: float = 0.0
    avg_cpu_percent: float = 0.0
    avg_memory_mb: float = 0.0
    
    # Ollama process metrics (if available)
    ollama_peak_cpu: float = 0.0
    ollama_peak_memory_mb: float = 0.0
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EpisodeProfileStats:
    """Aggregated statistics for all LLM calls on an episode."""
    episode_name: str
    total_calls: int
    successful_calls: int
    failed_calls: int
    total_llm_time_sec: float
    avg_call_time_sec: float
    min_call_time_sec: float
    max_call_time_sec: float
    total_spans_found: int
    peak_memory_mb: float
    avg_memory_mb: float
    
    def to_dict(self) -> dict:
        return asdict(self)


class LLMMonitor:
    """Monitor and track LLM performance across episodes."""
    
    def __init__(self, profile_dir: str = "data/llm_profiles"):
        self.profile_dir = Path(profile_dir)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.current_episode: Optional[str] = None
        self.call_metrics: List[LLMCallMetrics] = []
        self.monitoring = False
        self.ollama_process: Optional[psutil.Process] = None
    
    def start_episode(self, episode_name: str):
        """Start monitoring a new episode."""
        self.current_episode = episode_name
        self.call_metrics = []
        logger.info(f"Started LLM monitoring for: {episode_name}")
    
    def find_ollama_process(self) -> Optional[psutil.Process]:
        """Find the Ollama process if running."""
        try:
            for proc in psutil.process_iter(['pid', 'name']):
                if 'ollama' in proc.info['name'].lower():
                    logger.debug(f"Found Ollama process: PID {proc.info['pid']}")
                    return proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        return None
    
    def record_call(self, 
                   section_index: int,
                   section_start_ms: int,
                   section_end_ms: int,
                   duration_sec: float,
                   success: bool,
                   spans_returned: int = 0,
                   error_msg: Optional[str] = None):
        """Record metrics for a single LLM call.
        
        Args:
            section_index: Index of the section within episode
            section_start_ms: Start time of section in milliseconds
            section_end_ms: End time of section in milliseconds
            duration_sec: Time taken for the LLM call
            success: Whether the call succeeded
            spans_returned: Number of ad spans returned (if successful)
            error_msg: Error message if failed
        """
        if not self.current_episode:
            logger.warning("record_call called without active episode")
            return
        
        # Get resource metrics for this system
        try:
            proc_metrics = self._get_process_metrics()
            peak_cpu = proc_metrics.cpu_percent
            peak_mem = proc_metrics.memory_mb
            avg_cpu = proc_metrics.cpu_percent
            avg_mem = proc_metrics.memory_mb
        except Exception as e:
            logger.debug(f"Failed to get process metrics: {e}")
            peak_cpu = avg_cpu = 0.0
            peak_mem = avg_mem = 0.0
        
        # Get Ollama metrics if possible
        ollama_cpu = 0.0
        ollama_mem = 0.0
        try:
            if not self.ollama_process:
                self.ollama_process = self.find_ollama_process()
            if self.ollama_process:
                with self.ollama_process.oneshot():
                    ollama_cpu = self.ollama_process.cpu_percent(interval=0.1)
                    ollama_mem = self.ollama_process.memory_info().rss / (1024 * 1024)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            self.ollama_process = None
        
        metric = LLMCallMetrics(
            episode_name=self.current_episode,
            section_index=section_index,
            section_start_ms=section_start_ms,
            section_end_ms=section_end_ms,
            duration_sec=duration_sec,
            success=success,
            error_msg=error_msg,
            spans_returned=spans_returned,
            peak_cpu_percent=peak_cpu,
            peak_memory_mb=peak_mem,
            avg_cpu_percent=avg_cpu,
            avg_memory_mb=avg_mem,
            ollama_peak_cpu=ollama_cpu,
            ollama_peak_memory_mb=ollama_mem,
        )
        
        self.call_metrics.append(metric)
        
        # Log the call
        status = "✓" if success else "✗"
        logger.info(
            f"{status} Section {section_index} [{section_start_ms}ms-{section_end_ms}ms]: "
            f"{duration_sec:.1f}s, {spans_returned} spans found, "
            f"Memory: {peak_mem:.0f}MB, CPU: {peak_cpu:.1f}%"
        )
    
    def _get_process_metrics(self) -> ProcessMetrics:
        """Get current process resource metrics."""
        proc = psutil.Process(os.getpid())
        cpu_percent = proc.cpu_percent(interval=0.1)
        mem_info = proc.memory_info()
        memory_mb = mem_info.rss / (1024 * 1024)
        memory_percent = proc.memory_percent()
        
        return ProcessMetrics(
            timestamp=datetime.now().isoformat(),
            cpu_percent=cpu_percent,
            memory_mb=memory_mb,
            memory_percent=memory_percent,
        )
    
    def save_episode_profile(self) -> Optional[str]:
        """Save metrics for the current episode to disk.
        
        Returns:
            Path to saved profile file, or None if no episode active
        """
        if not self.current_episode or not self.call_metrics:
            logger.warning("No active episode or no metrics to save")
            return None
        
        profile_path = self.profile_dir / f"{self.current_episode}_llm_profile.json"
        
        try:
            profile_data = {
                'episode_name': self.current_episode,
                'timestamp': datetime.now().isoformat(),
                'call_count': len(self.call_metrics),
                'calls': [m.to_dict() for m in self.call_metrics],
                'stats': self._compute_stats().to_dict(),
            }
            
            with open(profile_path, 'w', encoding='utf-8') as f:
                json.dump(profile_data, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Saved LLM profile to: {profile_path}")
            return str(profile_path)
        except Exception as e:
            logger.warning(f"Failed to save episode profile: {e}")
            return None
    
    def _compute_stats(self) -> EpisodeProfileStats:
        """Compute aggregate statistics for current episode."""
        if not self.call_metrics:
            return EpisodeProfileStats(
                episode_name=self.current_episode or "unknown",
                total_calls=0,
                successful_calls=0,
                failed_calls=0,
                total_llm_time_sec=0.0,
                avg_call_time_sec=0.0,
                min_call_time_sec=0.0,
                max_call_time_sec=0.0,
                total_spans_found=0,
                peak_memory_mb=0.0,
                avg_memory_mb=0.0,
            )
        
        successful = [m for m in self.call_metrics if m.success]
        failed = [m for m in self.call_metrics if not m.success]
        durations = [m.duration_sec for m in successful]
        memories = [m.peak_memory_mb for m in self.call_metrics]
        
        return EpisodeProfileStats(
            episode_name=self.current_episode or "unknown",
            total_calls=len(self.call_metrics),
            successful_calls=len(successful),
            failed_calls=len(failed),
            total_llm_time_sec=sum(durations),
            avg_call_time_sec=sum(durations) / len(durations) if durations else 0.0,
            min_call_time_sec=min(durations) if durations else 0.0,
            max_call_time_sec=max(durations) if durations else 0.0,
            total_spans_found=sum(m.spans_returned for m in successful),
            peak_memory_mb=max(memories) if memories else 0.0,
            avg_memory_mb=sum(memories) / len(memories) if memories else 0.0,
        )
    
    def print_episode_summary(self):
        """Print a summary of the current episode's LLM metrics."""
        stats = self._compute_stats()
        
        print("\n" + "="*100)
        print("LLM PERFORMANCE SUMMARY")
        print("="*100)
        print(f"Episode: {stats.episode_name}")
        print(f"Total calls: {stats.total_calls} ({stats.successful_calls} success, {stats.failed_calls} failed)")
        print(f"Total LLM time: {stats.total_llm_time_sec:.1f}s")
        print(f"Avg call time: {stats.avg_call_time_sec:.1f}s")
        print(f"Call time range: {stats.min_call_time_sec:.1f}s - {stats.max_call_time_sec:.1f}s")
        print(f"Total spans found: {stats.total_spans_found}")
        print(f"Peak memory: {stats.peak_memory_mb:.0f}MB")
        print(f"Avg memory: {stats.avg_memory_mb:.0f}MB")
        print("="*100 + "\n")


# Global monitor instance
_monitor = LLMMonitor()


def get_monitor() -> LLMMonitor:
    """Get the global LLM monitor instance."""
    return _monitor
