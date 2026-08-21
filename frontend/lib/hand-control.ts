import type { NormalizedLandmark } from "@mediapipe/tasks-vision";
import type { AxisInput } from "./teleop";

export type HandControlState = {
  detected: boolean;
  calibrated: boolean;
  active: boolean;
  palmX: number;
  palmY: number;
  scaleRatio: number;
  openness: number;
  yawDegrees: number;
  gesture: "open" | "fist" | "like" | "neutral";
  clutched: boolean;
  rollVelocityDeg: number;
  rotationActive: boolean;
  input: AxisInput;
};

type Calibration = { x: number; y: number; scale: number; yaw: number };

const ZERO_INPUT: AxisInput = { linear: [0, 0, 0], angular: [0, 0, 0], gripper: -1 };
const PALM = [0, 5, 9, 13, 17];
const TIPS = [8, 12, 16, 20];
const MCPS = [5, 9, 13, 17];
const PIPS = [6, 10, 14, 18];

const clamp = (value: number, low = -1, high = 1) => Math.min(high, Math.max(low, value));
const distance = (a: NormalizedLandmark, b: NormalizedLandmark) => Math.hypot(a.x - b.x, a.y - b.y);
const wrapAngle = (angle: number) => Math.atan2(Math.sin(angle), Math.cos(angle));

function palmHeading(landmarks?: NormalizedLandmark[]): number | null {
  if (!landmarks || landmarks.length < 21) return null;
  const index = landmarks[5];
  const pinky = landmarks[17];
  const acrossX = pinky.x - index.x;
  const acrossZ = pinky.z - index.z;
  if (Math.hypot(acrossX, acrossZ) < 1e-6) return null;
  // The across-palm vector rotates in camera X/Z when the wrist turns left or
  // right. This is more symmetric than deriving a noisy palm normal.
  return Math.atan2(acrossZ, acrossX);
}

function deadzone(value: number, threshold: number): number {
  if (Math.abs(value) <= threshold) return 0;
  return Math.sign(value) * (Math.abs(value) - threshold) / (1 - threshold);
}

function features(landmarks: NormalizedLandmark[]) {
  const palmX = PALM.reduce((sum, index) => sum + landmarks[index].x, 0) / PALM.length;
  const palmY = PALM.reduce((sum, index) => sum + landmarks[index].y, 0) / PALM.length;
  // Palm-only distances remain useful when fingers open/close. Using several
  // baselines makes relative depth less sensitive to one noisy landmark.
  const scale = [
    distance(landmarks[5], landmarks[17]),
    distance(landmarks[0], landmarks[9]),
    distance(landmarks[0], landmarks[5]),
    distance(landmarks[0], landmarks[17]),
  ].reduce((sum, value) => sum + value, 0) / 4;
  const openness = TIPS.reduce(
    (sum, tip, index) => sum + distance(landmarks[tip], landmarks[MCPS[index]]) / Math.max(scale, 1e-6),
    0,
  ) / TIPS.length;
  // A straight finger places its tip clearly farther from the wrist than its
  // middle joint. This ratio is scale-invariant and survives camera depth
  // changes better than a fixed fingertip-to-palm distance.
  const fingerStraightness = TIPS.map(
    (tip, index) =>
      distance(landmarks[tip], landmarks[0]) /
      Math.max(distance(landmarks[PIPS[index]], landmarks[0]), 1e-6),
  );
  const thumbExtension = distance(landmarks[4], landmarks[2]) / Math.max(scale, 1e-6);
  const openFingerCount = fingerStraightness.filter((value) => value > 1.22).length;
  const closedFingerCount = fingerStraightness.filter((value) => value < 1.12).length;
  const thumbPointsUp = landmarks[4].y < landmarks[2].y - 0.035;
  const gesture: HandControlState["gesture"] =
    closedFingerCount >= 3 && thumbExtension > 0.72 && thumbPointsUp
      ? "like"
      : closedFingerCount >= 3
        ? "fist"
        : openFingerCount >= 3
          ? "open"
          : "neutral";
  const yaw = Math.atan2(landmarks[17].y - landmarks[5].y, landmarks[17].x - landmarks[5].x);
  return { palmX, palmY, scale, openness, thumbExtension, gesture, yaw };
}

export class HandCommandMapper {
  private calibration: Calibration | null = null;
  private filtered: ReturnType<typeof features> | null = null;
  private previousControl: ReturnType<typeof features> | null = null;
  private command: [number, number, number] = [0, 0, 0];
  private previousHandAngle: number | null = null;
  private previousRotationAt: number | null = null;
  private rollVelocityDeg = 0;
  private rotationCommand = 0;
  private rotationActive = false;
  private rotationStartFrames = 0;
  private rotationStopFrames = 0;
  private gripperClosed = false;
  private openFrames = 0;
  private closedFrames = 0;
  private likeFrames = 0;
  private releaseFrames = 0;
  private clutched = false;

  calibrate(landmarks: NormalizedLandmark[]) {
    const value = features(landmarks);
    this.filtered = value;
    this.previousControl = value;
    this.command = [0, 0, 0];
    this.resetRotation();
    this.calibration = { x: value.palmX, y: value.palmY, scale: value.scale, yaw: value.yaw };
  }

  reset() {
    this.calibration = null;
    this.filtered = null;
    this.previousControl = null;
    this.command = [0, 0, 0];
    this.resetRotation();
    this.openFrames = 0;
    this.closedFrames = 0;
    this.likeFrames = 0;
    this.releaseFrames = 0;
    this.clutched = false;
  }

  update(
    landmarks: NormalizedLandmark[] | undefined,
    active: boolean,
    frameAtMs?: number,
    worldLandmarks?: NormalizedLandmark[],
  ): HandControlState {
    if (!landmarks || landmarks.length < 21) {
      this.previousControl = null;
      this.command = [0, 0, 0];
      this.resetRotation();
      return { ...this.empty(), detected: false, active: false };
    }

    const current = features(landmarks);
    const landmarkMotion = this.filtered
      ? Math.hypot(current.palmX - this.filtered.palmX, current.palmY - this.filtered.palmY) +
        Math.abs(Math.log(Math.max(current.scale, 1e-6) / Math.max(this.filtered.scale, 1e-6)))
      : 0;
    const alpha = clamp(0.24 + landmarkMotion * 8, 0.24, 0.68);
    this.filtered = this.filtered
      ? {
          palmX: alpha * current.palmX + (1 - alpha) * this.filtered.palmX,
          palmY: alpha * current.palmY + (1 - alpha) * this.filtered.palmY,
          scale: alpha * current.scale + (1 - alpha) * this.filtered.scale,
          openness: alpha * current.openness + (1 - alpha) * this.filtered.openness,
          thumbExtension: alpha * current.thumbExtension + (1 - alpha) * this.filtered.thumbExtension,
          gesture: current.gesture,
          yaw: this.filtered.yaw + alpha * wrapAngle(current.yaw - this.filtered.yaw),
        }
      : current;

    const f = this.filtered;
    const currentHandAngle = palmHeading(worldLandmarks);
    const rotationAt = frameAtMs ?? performance.now();
    const justReleasedClutch = this.updateGesture(f.gesture);
    const base = this.calibration;
    if (!base || !active || this.clutched || justReleasedClutch) {
      this.previousControl = f;
      this.command = [0, 0, 0];
      this.resetRotation(currentHandAngle, rotationAt);
      return { ...this.empty(), detected: true, calibrated: Boolean(base), active: Boolean(active && base), palmX: f.palmX, palmY: f.palmY, scaleRatio: base ? f.scale / base.scale : 1, openness: f.openness, yawDegrees: f.yaw * 180 / Math.PI, gesture: f.gesture, clutched: this.clutched, input: { ...ZERO_INPUT, gripper: this.gripperClosed ? 1 : -1 } };
    }

    // Direct motion between frames. During clutch `previousControl` keeps
    // following the hand, so releasing 👍 at a new centre creates no jump.
    const previous = this.previousControl ?? f;
    this.previousControl = f;
    const rawRight = deadzone(-(f.palmX - previous.palmX) * 42, 0.055);
    const rawUp = deadzone(-(f.palmY - previous.palmY) * 42, 0.055);
    const rawForward = deadzone(
      Math.log(Math.max(f.scale, 1e-6) / Math.max(previous.scale, 1e-6)) * 30,
      0.07,
    );

    // Smooth commands as well as landmarks. This suppresses single-frame
    // landmark jumps while retaining a responsive start/stop feel.
    const commandMagnitude = Math.max(Math.abs(rawForward), Math.abs(rawRight), Math.abs(rawUp));
    const commandAlpha = 0.48 + 0.24 * commandMagnitude;
    // Simulator camera convention (same as keyboard controls): linear[0] is
    // forward/back, linear[1] is screen left/right, linear[2] is up/down.
    // Keep this ordering explicit; swapping the first two makes a right-hand
    // gesture look like forward motion in the front camera.
    this.command = [rawForward, rawRight, rawUp].map(
      (value, index) => commandAlpha * value + (1 - commandAlpha) * this.command[index],
    ) as [number, number, number];
    this.command = this.command.map((value) => Math.abs(value) < 0.035 ? 0 : clamp(value)) as [number, number, number];
    this.updateRotation(currentHandAngle, rotationAt);

    return {
      detected: true,
      calibrated: true,
      active: true,
      palmX: f.palmX,
      palmY: f.palmY,
      scaleRatio: f.scale / base.scale,
      openness: f.openness,
      yawDegrees: f.yaw * 180 / Math.PI,
      gesture: f.gesture,
      clutched: false,
      rollVelocityDeg: this.rollVelocityDeg,
      rotationActive: this.rotationActive,
      input: {
        linear: this.command,
        // Direct Z-axis rotation: turning the hand clockwise/counter-clockwise
        // in the camera plane drives drz while XYZ remains independent.
        angular: [0, 0, this.rotationCommand],
        gripper: this.gripperClosed ? 1 : -1,
      },
    };
  }

  private updateRotation(currentAngle: number | null, at: number) {
    if (currentAngle === null) {
      this.resetRotation();
      return;
    }
    const previousAngle = this.previousHandAngle;
    const previousAt = this.previousRotationAt;
    this.previousHandAngle = currentAngle;
    this.previousRotationAt = at;
    if (previousAngle === null || previousAt === null) {
      this.rollVelocityDeg = 0;
      this.rotationCommand = 0;
      return;
    }
    const dt = (at - previousAt) / 1000;
    if (dt <= 0 || dt > 0.15) {
      this.rollVelocityDeg = 0;
      this.rotationCommand = 0;
      return;
    }

    const delta = wrapAngle(currentAngle - previousAngle);
    const velocity = delta / dt;
    this.rollVelocityDeg = velocity * 180 / Math.PI;

    const speed = Math.abs(velocity);
    const startThreshold = 3 * Math.PI / 180;
    const stopThreshold = 2 * Math.PI / 180;
    if (!this.rotationActive) {
      this.rotationStartFrames = speed >= startThreshold ? this.rotationStartFrames + 1 : 0;
      if (this.rotationStartFrames >= 2) {
        this.rotationActive = true;
        this.rotationStopFrames = 0;
      }
    } else {
      this.rotationStopFrames = speed < stopThreshold ? this.rotationStopFrames + 1 : 0;
      if (this.rotationStopFrames >= 2) {
        this.rotationActive = false;
        this.rotationStartFrames = 0;
        this.rotationCommand = 0;
      }
    }

    if (!this.rotationActive) {
      this.rotationCommand = 0;
      return;
    }
    const robotVelocity = -velocity;
    const fullSpeed = 25 * Math.PI / 180;
    const target = clamp(robotVelocity / fullSpeed) * 0.3;
    if (this.rotationCommand !== 0 && Math.sign(target) !== Math.sign(this.rotationCommand)) {
      // Reversing direction is a recovery action: discard the old filtered
      // command immediately instead of making the user fight its momentum.
      this.rotationCommand = 0;
    }
    this.rotationCommand = 0.8 * target + 0.2 * this.rotationCommand;
    if (Math.abs(this.rotationCommand) < 0.005) this.rotationCommand = 0;
  }

  private resetRotation(currentAngle: number | null = null, at: number | null = null) {
    this.previousHandAngle = currentAngle;
    this.previousRotationAt = at;
    this.rollVelocityDeg = 0;
    this.rotationCommand = 0;
    this.rotationActive = false;
    this.rotationStartFrames = 0;
    this.rotationStopFrames = 0;
  }

  private updateGesture(gesture: HandControlState["gesture"]): boolean {
    // 👍 has priority because it otherwise resembles a closed fist.
    this.likeFrames = gesture === "like" ? this.likeFrames + 1 : 0;
    if (!this.clutched && this.likeFrames >= 6) {
      this.clutched = true;
      this.releaseFrames = 0;
    }
    if (this.clutched) {
      this.releaseFrames = gesture === "like" ? 0 : this.releaseFrames + 1;
      if (this.releaseFrames >= 6) {
        this.clutched = false;
        this.releaseFrames = 0;
        return true;
      }
      return false;
    }

    this.openFrames = gesture === "open" ? this.openFrames + 1 : 0;
    this.closedFrames = gesture === "fist" ? this.closedFrames + 1 : 0;
    if (this.openFrames >= 6) this.gripperClosed = false;
    if (this.closedFrames >= 6) this.gripperClosed = true;
    return false;
  }

  private empty(): HandControlState {
    return { detected: false, calibrated: Boolean(this.calibration), active: false, palmX: 0.5, palmY: 0.5, scaleRatio: 1, openness: 0, yawDegrees: 0, gesture: "neutral", clutched: this.clutched, rollVelocityDeg: this.rollVelocityDeg, rotationActive: this.rotationActive, input: { ...ZERO_INPUT, gripper: this.gripperClosed ? 1 : -1 } };
  }
}
