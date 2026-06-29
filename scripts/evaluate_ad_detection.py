#!/usr/bin/env python3
"""
Evaluate ad detection against ground-truth labels.

Usage:
    python scripts/evaluate_ad_detection.py <episode_name> [--ground-truth-only]

This script:
1. Loads ground-truth labels if they exist
2. Loads the latest prediction from preview mode
3. Compares them and shows precision/recall/F1
4. Shows which ads were detected vs. missed vs. false alarms

Example:
    python scripts/evaluate_ad_detection.py philosophize-this-episode_237_the_stoics_are_wrong_-_nietzsche_schopenhauer
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skipcastify.services.ground_truth import GroundTruthStore, GroundTruthLabel, evaluate_predictions
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def load_latest_prediction(episode_name: str) -> tuple[list, str]:
    """Load the latest prediction output from preview mode.
    
    Returns:
        (predictions_list, output_log_path)
    """
    # Look for a predictions file (we'll save this during preview)
    predictions_path = Path("data/podcasts/processed_preview") / f"{episode_name}_predictions.json"
    
    if not predictions_path.exists():
        logger.error(f"No predictions found at {predictions_path}")
        logger.info("Run preview first: python scripts/preview_episode.py <episode>")
        return None, str(predictions_path)
    
    try:
        with open(predictions_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get('predictions', []), str(predictions_path)
    except Exception as e:
        logger.error(f"Failed to load predictions: {e}")
        return None, str(predictions_path)


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/evaluate_ad_detection.py <episode_name> [--ground-truth-only]")
        sys.exit(1)
    
    episode_name = sys.argv[1]
    ground_truth_only = "--ground-truth-only" in sys.argv
    
    store = GroundTruthStore()
    
    # Load ground-truth labels
    labels = store.load_labels(episode_name)
    if labels is None:
        print(f"\n❌ No ground-truth labels found for: {episode_name}")
        print(f"\nTo create labels, manually edit: {store.get_label_path(episode_name)}")
        print("\nExample format (JSON):")
        print(json.dumps({
            "episode_name": episode_name,
            "ads": [
                {
                    "start_ms": 871760,
                    "end_ms": 875520,
                    "label": "ADVERTISEMENT",
                    "description": "Diet Coke ad read"
                },
                {
                    "start_ms": 909920,
                    "end_ms": 916880,
                    "label": "SPONSOR",
                    "description": "Subaru spot"
                }
            ]
        }, indent=2))
        sys.exit(1)
    
    print(f"\n{'='*100}")
    print(f"GROUND-TRUTH LABELS FOR: {episode_name}")
    print(f"{'='*100}\n")
    
    total_ad_time = 0
    for i, label in enumerate(labels, 1):
        duration_s = (label.end_ms - label.start_ms) / 1000
        total_ad_time += duration_s
        time_range = f"[{label.start_ms}ms-{label.end_ms}ms]"
        print(f"{i}. {time_range} ({duration_s:.1f}s) - {label.label}")
        if label.description:
            print(f"   {label.description}")
    
    print(f"\nTotal ad time: {total_ad_time:.1f}s")
    
    if ground_truth_only:
        return
    
    # Load predictions
    predictions, pred_path = load_latest_prediction(episode_name)
    if predictions is None:
        sys.exit(1)
    
    print(f"\n{'='*100}")
    print(f"PREDICTIONS ({len(predictions)} spans detected)")
    print(f"{'='*100}\n")
    
    for i, pred in enumerate(predictions, 1):
        start = int(pred.get('start_ms', 0))
        end = int(pred.get('end_ms', 0))
        duration_s = (end - start) / 1000
        label = pred.get('label', 'ADVERTISEMENT')
        rationale = pred.get('rationale', '')
        
        time_range = f"[{start}ms-{end}ms]"
        print(f"{i}. {time_range} ({duration_s:.1f}s) - {label}")
        if rationale:
            print(f"   Rationale: {rationale}")
    
    # Evaluate
    print(f"\n{'='*100}")
    print(f"EVALUATION")
    print(f"{'='*100}\n")
    
    metrics = evaluate_predictions(labels, predictions, overlap_threshold_ms=1000)
    
    print(f"Precision: {metrics.precision:.3f} ({metrics.tp} correct out of {metrics.tp + metrics.fp} predicted)")
    print(f"Recall:    {metrics.recall:.3f} ({metrics.tp} found out of {len(labels)} actual)")
    print(f"F1 Score:  {metrics.f1:.3f}")
    print(f"\nTrue Positives:   {metrics.tp}")
    print(f"False Positives:  {metrics.fp}")
    print(f"False Negatives:  {metrics.fn}")
    
    if metrics.fp > 0:
        print(f"\n⚠️  {metrics.fp} false alarms (predicted ads that don't match ground-truth)")
    
    if metrics.fn > 0:
        print(f"\n⚠️  {metrics.fn} missed ads (ground-truth ads that weren't detected)")


if __name__ == '__main__':
    main()
