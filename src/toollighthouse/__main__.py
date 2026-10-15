from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

from .config import Settings


def _safe_led_shutdown(controller):
    if controller is None:
        return
    try:
        controller.send_mask(0)
    except Exception:
        pass
    try:
        close = getattr(controller, "close", None)
        if close:
            close()
    except Exception:
        pass


def _camera_scan(limit=4):
    import cv2

    found = []
    for index in range(limit + 1):
        capture = cv2.VideoCapture(index)
        try:
            opened = bool(capture.isOpened())
            found.append({"index": index, "opened": opened})
        finally:
            capture.release()
    return found


def _parse_crop(value):
    try:
        crop = tuple(int(part.strip()) for part in value.split(","))
    except (TypeError, ValueError) as exc:
        raise SystemExit("--crop must be x,y,width,height") from exc
    if len(crop) != 4 or crop[2] <= 0 or crop[3] <= 0 or crop[0] < 0 or crop[1] < 0:
        raise SystemExit("--crop must be x,y,width,height with positive dimensions")
    return crop


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m toollighthouse")
    parser.add_argument("command", choices=["doctor", "test-camera", "calibrate", "download-model", "build-gallery", "test-model", "test-leds", "run", "serve", "capture-reference", "evaluate"])
    parser.add_argument("--camera", default="0")
    parser.add_argument("--video")
    parser.add_argument("--serial")
    parser.add_argument("--simulate-leds", action="store_true")
    parser.add_argument("--mode", choices=["mock", "real"], default=None)
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default=None)
    parser.add_argument("--config")
    parser.add_argument("--inventory")
    parser.add_argument("--model-path", default=None)
    parser.add_argument("--gallery")
    parser.add_argument("--text-weight", type=float, default=None)
    parser.add_argument("--gallery-mode", choices=["text-only", "image-only", "hybrid"], default=None)
    parser.add_argument("--classifier-threshold", type=float, default=None)
    parser.add_argument("--classifier-margin", type=float, default=None)
    parser.add_argument("--class-id")
    parser.add_argument("--references")
    parser.add_argument("--image")
    parser.add_argument("--output")
    parser.add_argument("--crop", help="reference crop as x,y,width,height")
    parser.add_argument("--pairing-key")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--scan", action="store_true", help="scan camera indices 0 through 4")
    parser.add_argument("--check-cameras", action="store_true", help="include camera scan in doctor")
    parser.add_argument("--confirm-inventory", action="store_true", help="required before driving physical LEDs")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--ssl-certfile")
    parser.add_argument("--ssl-keyfile")
    args = parser.parse_args(argv)
    if bool(args.ssl_certfile) != bool(args.ssl_keyfile):
        parser.error("--ssl-certfile and --ssl-keyfile must be provided together")
    if (args.ssl_certfile or args.ssl_keyfile) and args.command != "serve":
        parser.error("TLS certificate options are only valid with the serve command")
    if args.command == "serve" and args.ssl_certfile:
        path_type = __import__("pathlib").Path
        if not path_type(args.ssl_certfile).is_file() or not path_type(args.ssl_keyfile).is_file():
            parser.error("--ssl-certfile and --ssl-keyfile must point to existing files")
    if args.max_frames < 0:
        parser.error("--max-frames must be non-negative")
    if args.simulate_leds and args.serial:
        parser.error("--simulate-leds cannot be combined with --serial")
    settings = Settings.from_file(args.config) if args.config else Settings()
    settings.mode = args.mode or settings.mode
    if args.device is not None:
        settings.device = args.device
    if args.model_path is not None:
        settings.model_path = args.model_path
    if args.gallery is not None:
        settings.gallery_path = args.gallery
    if args.text_weight is not None:
        if not 0.0 <= args.text_weight <= 1.0:
            raise SystemExit("--text-weight must be between 0 and 1")
        settings.text_weight = args.text_weight
    if args.gallery_mode is not None:
        settings.gallery_mode = args.gallery_mode
    if args.classifier_threshold is not None:
        settings.classifier_threshold = args.classifier_threshold
    if args.classifier_margin is not None:
        settings.classifier_margin = args.classifier_margin
    if args.classifier_threshold is not None or args.classifier_margin is not None:
        threshold, margin = settings.classifier_parameters()
        settings.classifier_profiles = {**settings.classifier_profiles, settings.gallery_mode: {"threshold": args.classifier_threshold if args.classifier_threshold is not None else threshold, "margin": args.classifier_margin if args.classifier_margin is not None else margin}}
    settings.__post_init__()
    if args.confirm_inventory:
        settings.inventory_confirmed = True
    if args.inventory:
        from .config import Inventory
        settings.inventory = Inventory.from_file(args.inventory)
    if args.pairing_key:
        settings.pairing_key = args.pairing_key
    import os
    settings.pairing_key = os.getenv("TOOLLIGHTHOUSE_PAIRING_KEY", os.getenv("TOOL_LIGHTHOUSE_PAIRING_KEY", settings.pairing_key))
    if args.serial and settings.mode == "mock":
        raise SystemExit("physical serial control is unavailable in mock mode; use --mode real")
    if args.command == "test-leds" and not args.simulate_leds and not args.serial:
        raise SystemExit("test-leds requires --serial or --simulate-leds")
    if args.command == "doctor":
        file_path = __import__("pathlib").Path
        model_path = file_path(settings.model_path)
        background_path = file_path(settings.background_path) if settings.background_path else None
        roi_path = file_path(settings.roi_path) if settings.roi_path else None
        report = {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "mode": settings.mode,
            "device": settings.device,
            "model_path": settings.model_path,
            "model_present": model_path.exists(),
            "gallery_present": bool(settings.gallery_path and file_path(settings.gallery_path).exists()),
            "background_present": bool(background_path and background_path.exists()),
            "roi_present": bool(roi_path and roi_path.exists()),
            "cuda": None,
            "gpu": None,
            "serial_port": args.serial,
            "fastapi": None,
            "pyserial": None,
            "sentence_transformers": None,
            "transformers": None,
            "torch": None,
            "torchvision": None,
            "huggingface_hub": None,
        }
        try:
            import cv2
            report["opencv"] = cv2.__version__
        except ImportError:
            report["opencv"] = None
        from importlib.metadata import PackageNotFoundError, version
        for distribution, key in (("fastapi", "fastapi"), ("pyserial", "pyserial"), ("sentence-transformers", "sentence_transformers"), ("transformers", "transformers"), ("torch", "torch"), ("torchvision", "torchvision"), ("huggingface-hub", "huggingface_hub")):
            try:
                report[key] = version(distribution)
            except PackageNotFoundError:
                pass
        try:
            import torch
            report["cuda"] = bool(torch.cuda.is_available())
            report["gpu"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
        except ImportError:
            pass
        try:
            from serial.tools import list_ports
            report["serial_ports"] = [port.device for port in list_ports.comports() if port.device]
        except ImportError:
            report["serial_ports"] = []
        if args.check_cameras:
            report["cameras"] = _camera_scan()
        print(json.dumps(report, indent=2))
        return 0
    if args.command == "serve":
        import uvicorn
        from .api import create_app
        if settings.auth_required and not settings.pairing_key:
            raise SystemExit("set TOOL_LIGHTHOUSE_PAIRING_KEY or TOOLLIGHTHOUSE_PAIRING_KEY before serving")
        serial = None
        if args.serial:
            if not settings.inventory_confirmed or settings.inventory.example or not (args.inventory or settings.inventory_path) or any(c.location == "unconfirmed" for c in settings.inventory.categories):
                raise SystemExit("refusing physical LED control: provide a confirmed --inventory and pass --confirm-inventory")
            from .serial_transport import SerialTransport
            serial = SerialTransport(args.serial)
            serial.open()
        from .pipeline import Pipeline
        # Safe startup keeps the dashboard available while calibration/model
        # assets are still missing; the admin can activate real mode later.
        uvicorn_options = {"host": args.host, "port": args.port}
        if args.ssl_certfile:
            uvicorn_options.update(ssl_certfile=args.ssl_certfile, ssl_keyfile=args.ssl_keyfile)
        uvicorn.run(
            create_app(settings, Pipeline(settings, serial=serial, safe_startup=True), config_path=args.config),
            **uvicorn_options,
        )
        return 0
    if args.command == "test-leds":
        from .serial_transport import SerialTransport, SimulatedSerial
        if args.serial and (not args.confirm_inventory or not args.inventory or settings.inventory.example or any(c.location == "unconfirmed" for c in settings.inventory.categories)):
            raise SystemExit("refusing physical LED control: provide --inventory and --confirm-inventory")
        controller = SimulatedSerial() if args.simulate_leds else SerialTransport(args.serial)
        try:
            if not controller.simulated:
                controller.open()
            for mask in (0, 511, 0):
                if not controller.send_mask(mask):
                    raise SystemExit(f"no matching ACK for MASK:{mask}")
            print(json.dumps(controller.status()))
            return 0
        finally:
            _safe_led_shutdown(controller)
    if args.command in {"run", "test-camera", "calibrate"}:
        from .camera import OpenCVCamera
        if args.command == "test-camera" and args.scan:
            print(json.dumps({"cameras": _camera_scan()}))
            return 0
        camera = OpenCVCamera(args.video if args.video else args.camera)
        try:
            camera.open()
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from exc
        if args.command == "test-camera":
            frame = camera.read()
            camera.close()
            print(json.dumps({"source": args.video or args.camera, "opened": frame is not None, "width": int(frame.shape[1]) if frame is not None else None, "height": int(frame.shape[0]) if frame is not None else None}))
            return 0
        from .pipeline import Pipeline
        from .overlay import draw_overlay
        from .serial_transport import SimulatedSerial
        serial = SimulatedSerial() if args.simulate_leds else None
        if args.serial:
            if not args.confirm_inventory or not args.inventory or settings.inventory.example or any(c.location == "unconfirmed" for c in settings.inventory.categories):
                camera.close()
                raise SystemExit("refusing physical LED control: provide --inventory and --confirm-inventory")
            from .serial_transport import SerialTransport
            serial = SerialTransport(args.serial)
            try:
                serial.open()
            except Exception:
                camera.close()
                _safe_led_shutdown(serial)
                raise
        try:
            pipeline = Pipeline(settings, serial=serial)
        except Exception:
            camera.close()
            _safe_led_shutdown(serial)
            raise
        if args.command == "run":
            from .camera import LatestFrameWorker, LatestInferenceWorker
            import time
            latest = {"frame": None, "result": None, "timestamp": 0.0}
            lock = __import__("threading").Lock()
            processed = {"count": 0}
            infer = LatestInferenceWorker(lambda image, timestamp, photo=False: pipeline.process(image, timestamp, photo))
            def classify(frame, timestamp):
                future = infer.submit(frame, timestamp, False)
                try:
                    result = future.result(timeout=settings.inference_timeout_seconds)
                    with lock:
                        latest.update(result=result, timestamp=timestamp)
                        processed["count"] += 1
                except Exception:
                    pass
            capture_worker = LatestFrameWorker(classify)
            capture_worker.start()
            captured = 0
            last_frame, last_timestamp = None, None
            playback_period = 0.0
            if getattr(camera, "capture", None) is not None:
                import cv2
                fps = float(camera.capture.get(cv2.CAP_PROP_FPS) or 0.0)
                if fps > 0.0:
                    playback_period = 1.0 / fps
            next_playback = time.monotonic()
            try:
                while args.max_frames <= 0 or captured < args.max_frames:
                    frame = camera.read()
                    if frame is None:
                        break
                    last_frame, last_timestamp = frame, time.time()
                    pipeline.metrics.observe_capture(last_timestamp)
                    capture_worker.submit(frame, last_timestamp)
                    with lock: result = latest["result"]
                    if result and not args.headless:
                        import cv2
                        cv2.imshow("ToolLighthouse", draw_overlay(frame, result["detections"], pipeline.metrics.as_dict(), pipeline.serial.status()))
                        pipeline.metrics.observe_display()
                        if cv2.waitKey(1) & 0xFF == ord("q"): break
                    captured += 1
                    if playback_period:
                        next_playback += playback_period
                        delay = next_playback - time.monotonic()
                        if delay > 0:
                            time.sleep(delay)
            finally:
                capture_worker.stop()
                # A short video can reach EOF while the size-one capture slot
                # still contains its last frame. Submit that frame explicitly
                # before closing the inference worker so a file never reports
                # zero processed frames merely because capture was faster than
                # the model.
                if last_frame is not None and last_timestamp is not None and latest["timestamp"] != last_timestamp:
                    classify(last_frame, last_timestamp)
                infer.close(); camera.close()
                _safe_led_shutdown(serial)
                if not args.headless:
                    import cv2; cv2.destroyAllWindows()
            print(json.dumps({"frames": processed["count"], "captured_frames": captured, "mode": settings.mode, "latest_only": True, "metrics": pipeline.metrics.as_dict()}))
            return 0
        count = 0
        try:
            while args.max_frames <= 0 or count < args.max_frames:
                frame = camera.read()
                if frame is None:
                    break
                if args.command == "calibrate":
                    pipeline.segmenter.set_background(frame)
                    if not args.headless:
                        import cv2
                        selected = cv2.selectROI("ToolLighthouse ROI", frame, showCrosshair=True, fromCenter=False)
                        if selected[2] > 0 and selected[3] > 0:
                            pipeline.segmenter.roi = tuple(int(v) for v in selected)
                            settings.roi = pipeline.segmenter.roi
                    if args.output:
                        import cv2
                        from pathlib import Path
                        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
                        cv2.imwrite(args.output, frame)
                    if settings.background_path:
                        import cv2
                        from pathlib import Path
                        Path(settings.background_path).parent.mkdir(parents=True, exist_ok=True)
                        cv2.imwrite(settings.background_path, frame)
                    if settings.roi_path and settings.roi:
                        from pathlib import Path
                        Path(settings.roi_path).parent.mkdir(parents=True, exist_ok=True)
                        Path(settings.roi_path).write_text(json.dumps(list(settings.roi)), encoding="utf-8")
                    print(json.dumps({"calibrated": True, "roi": list(settings.roi) if settings.roi else None, "width": int(frame.shape[1]), "height": int(frame.shape[0])}))
                    break
                result = pipeline.process(frame)
                if not args.headless:
                    import cv2
                    cv2.imshow("ToolLighthouse", draw_overlay(frame, result["detections"], pipeline.metrics.as_dict(), pipeline.serial.status()))
                    pipeline.metrics.observe_display()
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
                count += 1
        finally:
            camera.close()
            if serial:
                try: serial.send_mask(0)
                finally: serial.close()
            if not args.headless:
                import cv2
                cv2.destroyAllWindows()
        print(json.dumps({"frames": count, "mode": settings.mode, "metrics": pipeline.metrics.as_dict()}))
        return 0
    if args.command == "download-model":
        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:
            raise SystemExit("install the AI dependencies with: python -m pip install -e '.[ai]'") from exc
        snapshot_download(repo_id="google/embeddinggemma-2", local_dir=settings.model_path)
        print(f"Downloaded google/embeddinggemma-2 to {settings.model_path}")
        return 0
    if args.command == "test-model":
        from pathlib import Path
        from PIL import Image
        from .embeddings import EmbeddingEngine
        import numpy as np
        sample = Path(args.image or "samples/objeto.jpg")
        image = Image.open(sample).convert("RGB") if sample.exists() else Image.new("RGB", (224, 224), (80, 120, 160))
        engine = EmbeddingEngine(settings.model_path, None if settings.device == "auto" else settings.device)
        image_vector = engine.encode_images([image])[0]
        text_vector = engine.encode_text(["a laboratory tool"])[0]
        print(json.dumps({"image_dimensions": len(image_vector), "text_dimensions": len(text_vector), "finite": bool(np.isfinite(image_vector).all() and np.isfinite(text_vector).all())}))
        return 0
    if args.command == "build-gallery":
        from pathlib import Path
        from .embeddings import EmbeddingEngine, references_fingerprint
        from .gallery import build_gallery
        mode = settings.gallery_mode
        root = Path(args.references or "data/references")
        files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}) if mode in {"image-only", "hybrid"} else []
        if mode in {"image-only", "hybrid"} and not files:
            raise SystemExit(f"no reference images found under {root}")
        engine = EmbeddingEngine(settings.model_path, None if settings.device == "auto" else settings.device)
        try:
            gallery, manifest = build_gallery(engine, settings.inventory, mode=mode, image_paths=files, text_weight=settings.text_weight)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        output = args.output or "data/gallery.json"
        descriptions = {c.class_id: list(c.descriptions) for c in settings.inventory.categories}
        names = {c.class_id: c.name for c in settings.inventory.categories}
        ids = {c.class_id for c in settings.inventory.categories}
        manifest.update({"paths": [str(p) for p in files], "references_root": str(root), "model_path": str(Path(settings.model_path).resolve())})
        fingerprint = references_fingerprint(files, settings.model_path, descriptions=descriptions, names=names, text_weight=settings.text_weight, model_identity=str(Path(settings.model_path).resolve()), preprocessing=manifest["preprocessing"], gallery_mode=mode, inventory_ids=ids)
        gallery.save(output, fingerprint, manifest=manifest)
        print(json.dumps({"output": output, "classes": sorted(ids), "images": len(files), "text_prompts": manifest["text_count"], "gallery_mode": mode, "threshold": settings.classifier_parameters()[0], "margin": settings.classifier_parameters()[1]}))
        return 0
    if args.command == "capture-reference":
        if not args.class_id or not args.output:
            raise SystemExit("capture-reference requires --class-id and --output")
        if args.class_id not in settings.inventory.by_id:
            raise SystemExit(f"unknown inventory class: {args.class_id}")
        from pathlib import Path
        from .camera import OpenCVCamera
        import cv2
        from .segmentation import Segmenter
        camera = OpenCVCamera(args.video or args.camera)
        try:
            try:
                camera.open()
            except RuntimeError as exc:
                raise SystemExit(str(exc)) from exc
            frame = camera.read()
            if frame is None:
                raise SystemExit("camera produced no frame")
            crop = _parse_crop(args.crop) if args.crop else None
            if crop is None:
                background_path = Path(settings.background_path) if settings.background_path else None
                if background_path is None or not background_path.exists():
                    raise SystemExit("capture-reference requires a calibrated background; use --crop only with a known crop")
                background = cv2.imread(str(background_path), cv2.IMREAD_COLOR)
                if background is None:
                    raise SystemExit(f"unable to read calibrated background: {background_path}")
                roi = settings.roi
                if roi is None and settings.roi_path and Path(settings.roi_path).exists():
                    roi = tuple(json.loads(Path(settings.roi_path).read_text(encoding="utf-8")))
                segmenter = Segmenter(settings.segmentation_min_area, settings.segmentation_max_area, require_background=True, roi=roi)
                segmenter.set_background(background)
                segments = segmenter.detect(frame)
                if len(segments) == 1:
                    crop = segments[0].bbox
                elif args.headless:
                    raise SystemExit("headless capture requires exactly one detected tool or an explicit --crop")
            if crop is None:
                selected = cv2.selectROI("ToolLighthouse reference", frame, showCrosshair=True, fromCenter=False)
                cv2.destroyWindow("ToolLighthouse reference")
                crop = tuple(int(value) for value in selected)
            x, y, width, height = crop
            frame_height, frame_width = frame.shape[:2]
            if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > frame_width or y + height > frame_height:
                raise SystemExit("reference crop must be inside the captured frame")
            reference = frame[y:y + height, x:x + width].copy()
        finally:
            camera.close()
        output = Path(args.output) / args.class_id
        output.mkdir(parents=True, exist_ok=True)
        import time
        stem = f"reference_{time.strftime('%Y%m%d_%H%M%S')}_{time.time_ns() % 1_000_000:06d}"
        target = output / f"{stem}.jpg"
        suffix = 1
        while target.exists():
            target = output / f"{stem}_{suffix}.jpg"
            suffix += 1
        if not cv2.imwrite(str(target), reference):
            raise SystemExit(f"unable to write reference image: {target}")
        print(json.dumps({"output": str(target), "class_id": args.class_id}))
        return 0
    if args.command == "evaluate":
        from pathlib import Path
        from PIL import Image
        from .embeddings import EmbeddingEngine
        from .gallery import load_validated_gallery
        from .classifier import SimilarityClassifier
        root = Path(args.references or "data/evaluation")
        gallery_path = args.gallery or "data/gallery.json"
        files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
        if not files or not Path(gallery_path).exists():
            raise SystemExit("evaluate requires evaluation images grouped by class and a built --gallery")
        import json as json_module
        try:
            gallery = load_validated_gallery(gallery_path, inventory=settings.inventory, model_path=settings.model_path, gallery_mode=settings.gallery_mode, text_weight=settings.text_weight)
        except (OSError, ValueError, KeyError) as exc:
            raise SystemExit(f"gallery cache is unavailable or stale; rebuild it: {exc}") from exc
        engine = EmbeddingEngine(settings.model_path, None if settings.device == "auto" else settings.device)
        vectors = engine.encode_images([Image.open(path).convert("RGB") for path in files])
        threshold, margin = settings.classifier_parameters()
        classifier = SimilarityClassifier(gallery.references, threshold, margin, gallery_mode=settings.gallery_mode)
        predictions = [classifier.predict(vector, index + 1) for index, vector in enumerate(vectors)]
        labels = sorted(gallery.references)
        known_pairs = [(prediction, path) for prediction, path in zip(predictions, files) if path.parent.name in gallery.references]
        unknown_pairs = [(prediction, path) for prediction, path in zip(predictions, files) if path.parent.name not in gallery.references]
        correct = sum(prediction.class_id == path.parent.name for prediction, path in known_pairs)
        confusion = {label: {candidate: 0 for candidate in labels + ["unknown"]} for label in labels}
        for prediction, path in known_pairs:
            confusion[path.parent.name][prediction.class_id or "unknown"] += 1
        false_accepts = sum(prediction.class_id is not None for prediction, _ in unknown_pairs)
        report = {"images": len(files), "known_images": len(known_pairs), "unknown_images": len(unknown_pairs), "correct": correct, "accuracy": correct / len(known_pairs) if known_pairs else None, "unknown_rejections": sum(prediction.class_id is None for prediction, _ in unknown_pairs), "false_accept_rate": false_accepts / len(unknown_pairs) if unknown_pairs else None, "confusion": confusion, "gallery_mode": settings.gallery_mode, "threshold": threshold, "margin": margin, "samples": [{"file": str(path), "expected": path.parent.name, "top1": prediction.class_id, "similarity_top1": prediction.similarity_top1, "similarity_top2": prediction.similarity_top2, "margin": prediction.margin, "validation": prediction.validation} for prediction, path in zip(predictions, files)]}
        if args.output:
            Path(args.output).write_text(json_module.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
        return 0
    raise SystemExit(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
