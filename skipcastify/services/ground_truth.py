"""
Ground-truth ad detection labels for evaluation and testing.

Format: YAML files with manually-labeled ad spans for episodes.
Stored in data/labels/ground_truth/ directory.

Example:
    episode_name: "philosophize-this-episode_237_the_stoics_are_wrong_-_nietzsche_schopenhauer"
    ads:
      - start_ms: 871760
        end_ms: 875520
        label: "ADVERTISEMENT"
        description: "Diet Coke ad read"
      - start_ms: 909920
        end_ms: 916880
        label: "SPONSOR"
        description: "Subaru spot"
"""

import logging
import os
from pathlib import Path
from typing import List, Optional, Tuple
import json

logger = logging.getLogger(__name__)


class GroundTruthLabel:
    """A manually-labeled ground-truth ad span."""
    
    def __init__(self, start_ms: int, end_ms: int, label: str, description: str = ""):
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.label = label  # "ADVERTISEMENT", "SPONSOR", or "CONTENT"
        self.description = description  # Optional
    
    def to_dict(self) -> dict:
        result = {
            'start_ms': self.start_ms,
            'end_ms': self.end_ms,
            'label': self.label,
        }
        if self.description:  # Only include if present
            result['description'] = self.description
        return result
    
    @staticmethod
    def from_dict(d: dict) -> 'GroundTruthLabel':
        return GroundTruthLabel(
            start_ms=d['start_ms'],
            end_ms=d['end_ms'],
            label=d.get('label', 'ADVERTISEMENT'),
            description=d.get('description', '')
        )
    
    def __repr__(self):
        return f"GroundTruthLabel({self.start_ms}-{self.end_ms}, {self.label}, {self.description})"


class GroundTruthStore:
    """Manages ground-truth labels for episodes."""
    
    def __init__(self, labels_dir: str = "data/labels/ground_truth"):
        self.labels_dir = Path(labels_dir)
        self.labels_dir.mkdir(parents=True, exist_ok=True)
    
    def get_label_path(self, episode_name: str) -> Path:
        """Get the label file path for an episode."""
        return self.labels_dir / f"{episode_name}_labels.json"
    
    def load_labels(self, episode_name: str) -> Optional[List[GroundTruthLabel]]:
        """Load ground-truth labels for an episode.
        
        Returns:
            List of GroundTruthLabel objects, or None if not found
        """
        label_path = self.get_label_path(episode_name)
        if not label_path.exists():
            logger.debug(f"No ground-truth labels found for {episode_name}")
            return None
        
        try:
            with open(label_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            labels = []
            for item in data.get('ads', []):
                labels.append(GroundTruthLabel.from_dict(item))
            
            logger.info(f"Loaded {len(labels)} ground-truth labels for {episode_name}")
            return labels
        except Exception as e:
            logger.warning(f"Failed to load ground-truth labels: {e}")
            return None
    
    def save_labels(self, episode_name: str, labels: List[GroundTruthLabel]) -> None:
        """Save ground-truth labels for an episode.
        
        Args:
            episode_name: Name of the episode
            labels: List of GroundTruthLabel objects
        """
        label_path = self.get_label_path(episode_name)
        try:
            data = {
                'episode_name': episode_name,
                'ads': [label.to_dict() for label in labels]
            }
            
            with open(label_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Saved {len(labels)} ground-truth labels for {episode_name}")
        except Exception as e:
            logger.warning(f"Failed to save ground-truth labels: {e}")
    
    def has_labels(self, episode_name: str) -> bool:
        """Check if labels exist for an episode."""
        return self.get_label_path(episode_name).exists()
    
    def list_labeled(self) -> List[str]:
        """List all episodes with ground-truth labels."""
        labeled = []
        for f in self.labels_dir.glob("*_labels.json"):
            episode_name = f.name.replace("_labels.json", "")
            labeled.append(episode_name)
        return sorted(labeled)


class EvaluationMetrics:
    """Evaluation metrics for ad detection."""
    
    def __init__(self, 
                 true_positives: int = 0,
                 false_positives: int = 0,
                 false_negatives: int = 0,
                 true_negatives: int = 0):
        self.tp = true_positives
        self.fp = false_positives
        self.fn = false_negatives
        self.tn = true_negatives
    
    @property
    def precision(self) -> float:
        """Precision: TP / (TP + FP)"""
        denom = self.tp + self.fp
        return self.tp / denom if denom > 0 else 0.0
    
    @property
    def recall(self) -> float:
        """Recall: TP / (TP + FN)"""
        denom = self.tp + self.fn
        return self.tp / denom if denom > 0 else 0.0
    
    @property
    def f1(self) -> float:
        """F1 score: 2 * (precision * recall) / (precision + recall)"""
        p = self.precision
        r = self.recall
        denom = p + r
        return 2 * (p * r) / denom if denom > 0 else 0.0
    
    def __str__(self):
        return (f"Precision: {self.precision:.3f} | "
                f"Recall: {self.recall:.3f} | "
                f"F1: {self.f1:.3f} | "
                f"TP={self.tp} FP={self.fp} FN={self.fn} TN={self.tn}")


def evaluate_predictions(ground_truth: List[GroundTruthLabel], 
                        predictions: List[dict],
                        overlap_threshold_ms: int = 1000) -> EvaluationMetrics:
    """Evaluate predicted ad spans against ground-truth labels.
    
    Args:
        ground_truth: List of GroundTruthLabel objects
        predictions: List of dicts with keys: start_ms, end_ms, label, rationale
        overlap_threshold_ms: Minimum overlap (ms) to consider a match
    
    Returns:
        EvaluationMetrics with TP, FP, FN, TN counts
    """
    # Mark which ground-truth labels were matched
    matched_ground_truth = set()
    tp = 0
    fp = 0
    
    for pred in predictions:
        pred_start = int(pred.get('start_ms', 0))
        pred_end = int(pred.get('end_ms', 0))
        
        found_match = False
        for i, gt in enumerate(ground_truth):
            if i in matched_ground_truth:
                continue
            
            # Check for overlap
            overlap_start = max(pred_start, gt.start_ms)
            overlap_end = min(pred_end, gt.end_ms)
            overlap = max(0, overlap_end - overlap_start)
            
            if overlap >= overlap_threshold_ms:
                tp += 1
                matched_ground_truth.add(i)
                found_match = True
                break
        
        if not found_match:
            fp += 1
    
    # Unmatched ground-truth labels are false negatives
    fn = len(ground_truth) - len(matched_ground_truth)
    tn = 0  # We don't track true negatives easily in this context
    
    return EvaluationMetrics(true_positives=tp, false_positives=fp, false_negatives=fn, true_negatives=tn)
