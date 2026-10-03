"""Freeze-test the batch experiment; emit golden vectors and warmed host timings."""
import argparse
import hashlib
import json
import statistics
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import average_precision_score, roc_auc_score, f1_score
from threadpoolctl import threadpool_limits

from software.batch_model_reference import BatchModelReference


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=Path('genomic-dataset-pipeline/artifacts/model_v4_batch_seed_7/export_manifest.json'))
    parser.add_argument('--input', type=Path, default=Path('genomic-dataset-pipeline/data/processed/variants_model_int8.parquet'))
    parser.add_argument('--split', default='test', choices=('validation', 'test'))
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--vectors', type=Path, required=True)
    args = parser.parse_args()
    model = BatchModelReference(args.manifest)
    data = pd.read_parquet(args.input)
    data = data.loc[data['split'].eq(args.split)]
    features = data[model.manifest['feature_order']].to_numpy(dtype=np.int64)
    # Keep temporaries bounded for the integer reference.
    expected = np.concatenate([model.score(chunk) for chunk in np.array_split(features, 32)])
    torch.set_num_threads(4)
    checkpoint_path = args.manifest.parent / 'model_qat.pt'
    checkpoint = torch.load(checkpoint_path, map_location='cpu', weights_only=False)
    assert checkpoint['feature_order'] == model.manifest['feature_order']
    layers = []
    for i, (w, b, shift) in enumerate(model.layers):
        trained_w = torch.round(checkpoint['state_dict'][f'weights.{i}']).clamp(-127, 127)
        trained_b = torch.round(checkpoint['state_dict'][f'biases.{i}'])
        if i == 2:
            trained_b -= model.manifest['classification_threshold']
        np.testing.assert_array_equal(trained_w.numpy(), w)
        np.testing.assert_array_equal(trained_b.numpy(), b)
        layers.append((trained_w, trained_b, shift))
    with torch.no_grad():
        x = torch.tensor(features, dtype=torch.float32)
        for w, b, shift in layers:
            x = x @ w.T + b
            if shift:
                x = torch.clamp(torch.floor((x+(1 << (shift-1)))/(1 << shift)), 0, 127)
        np.testing.assert_array_equal(x[:, 0].numpy().astype(np.int64), expected)
    args.vectors.parent.mkdir(parents=True, exist_ok=True)
    args.vectors.write_text(''.join(bytes(int(v)&255 for v in row).hex()+
                                  int(score).to_bytes(4, 'big', signed=True).hex()+'\n'
                                  for row, score in zip(features, expected)))
    timings = []
    float_features = features.astype(np.float32)
    for threads in (1, 4):
        with threadpool_limits(limits=threads):
            for size in (32, 64, 1024):
                def infer():
                    return np.concatenate([model.score(float_features[i:i+size], np.float32)
                                           for i in range(0, len(features), size)])
                np.testing.assert_array_equal(infer(), expected)
                seconds = []
                for _ in range(5):
                    start = time.perf_counter()
                    scores = infer()
                    seconds.append(time.perf_counter()-start)
                    np.testing.assert_array_equal(scores, expected)
                timings.append({'threads': threads, 'batch_size': size, 'seconds': seconds,
                                'completed_variants_per_second': len(features)/statistics.median(seconds)})
                print(threads, size, f'{timings[-1]["completed_variants_per_second"]:.1f} variants/s', flush=True)
    labels = data['label'].to_numpy()
    report = {'architecture': model.manifest['architecture'], 'split': args.split,
              'count': len(features), 'all_pytorch_integer_and_batched_scores_exact': True,
              'manifest_sha256': hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
              'checkpoint_sha256': hashlib.sha256(checkpoint_path.read_bytes()).hexdigest(),
              'average_precision': float(average_precision_score(labels, expected)),
              'roc_auc': float(roc_auc_score(labels, expected)),
              'f1': float(f1_score(labels, expected>=0)),
              'routing_recall': float((expected[labels==1]>=model.manifest['routing_threshold']).mean()),
              'nonzero_middle_weights': int(np.count_nonzero(model.layers[1][0])),
              'host_timings': timings,
              'timing_scope': 'prepared features to scores; excludes file I/O; five warmed repetitions'}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2)+'\n')
    print(f'Saved {args.report}; all {len(features)} scores exact')


if __name__ == '__main__':
    main()
