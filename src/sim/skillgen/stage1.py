"""ToolHang stage 1: insert the hook frame's rod into the stand's mount cavity.

The whole skill is closed loop on the *rod tip*, never on the end effector. The
grasp is never exact and the 187 mm from the grip to the tip amplifies any
residual wrist error, so every alignment step re-reads the tip's true pose from
the simulator and corrects the hand target by the leftover tip error.
"""
import time
import numpy as np
import robosuite.utils.transform_utils as T

from . import geometry as G
from .primitives import Servo, grasp_mat, body_geom_names, CLOSE, OPEN
from .telemetry import StepTelemetry, EpisodeResult

TABLE_TOP = 0.810
SAFE_Z = 1.26          # hand height that keeps the hung rod clear of the table
LIFT_CLEAR = 0.16
APPROACH_UP = 0.09
GRIP_TO_TIP = float(np.linalg.norm(G.GRIP_LOCAL - G.TIP_LOCAL))  # 187 mm
HOVER = 0.030          # tip height above the cavity mouth when aligning
# Push well past the depth that counts as seated. Releasing at the bare minimum
# leaves the rod shallow enough that opening the fingers and lifting drags it
# straight back out; the cavity is 150 mm deep, so there is room to commit.
INSERT_DEPTH = 0.130
# How far the hand sinks after opening, before moving clear. Bounded by the top
# of the stand: at release the hand sits ~57 mm above it, so 15 mm keeps ~40 mm
# of finger clearance.
RELEASE_SINK = 0.015
# Where along the rod the 7.5 mm square section begins, measured from the tip:
# cone to ~31 mm, then a 6.5 mm cylinder, then the square. Past this depth the
# fit tightens from the cone's 4 mm capture tolerance to 1.25 mm a side.
SQUARE_FROM_TIP = 0.030
# Lateral error the square section can be committed at. A quarter of the
# 1.25 mm clearance is held back for the drift that accrues during the descent.
SQUARE_LATERAL_OK = 0.0010
# Depth past which the square section is engaged enough for the bore to hold the
# rod parallel, so releasing and letting it slide beats pushing it. `is_seated`
# already treats 40 mm as home, and the observed jams all sat at 44 mm.
GRAVITY_FINISH_DEPTH = 0.040


class Stage1:
    def __init__(self, env, viewer=None, max_attempts=3, max_steps=2200, collect=False):
        self.env = env
        self.viewer = viewer
        self.max_attempts = max_attempts
        self.max_steps = max_steps
        self.servo = Servo(env, on_step=self._on_step)
        self.fgeoms = body_geom_names(env, "frame_root")
        self._fb = env.sim.model.body_name2id("frame_root")
        self.phase = "reach_grip"
        self.attempt = 1
        self._yaw_offset = 0.0
        self.cavity = None
        self.collect = collect
        # (state, action) pairs of the attempt currently running; only the
        # attempt that ends seated is worth exporting.
        self.record = None
        self.model_xml = None
        self.trace = []
        self._quit = False

    # ------------------------------------------------------------ readings
    def frame_pose(self):
        d = self.env.sim.data
        q = d.body_xquat[self._fb]
        return d.body_xpos[self._fb].copy(), T.quat2mat(np.array([q[1], q[2], q[3], q[0]]))

    def tip(self):
        p, R = self.frame_pose()
        return p + R @ G.TIP_LOCAL

    def rod_axis(self):
        _, R = self.frame_pose()
        return R @ G.ROD_AXIS_LOCAL

    def rod_yaw(self):
        """Rod cross-section yaw, folded into its 90 deg symmetry."""
        _, R = self.frame_pose()
        x = R @ np.array([1.0, 0.0, 0.0])
        a = np.arctan2(x[1], x[0]) - self.cavity.yaw
        return (a + np.pi / 4) % (np.pi / 2) - np.pi / 4

    def n_contacts(self):
        return int(self.env.sim.data.ncon)

    # ------------------------------------------------------------ telemetry
    def _on_step(self):
        tip = self.tip()
        lateral, tilt, _, depth = G.insertion_error(
            tip, self.rod_axis(), self.rod_yaw(), self.cavity
        )
        t = StepTelemetry(
            step=self.servo.steps,
            sim_time=float(self.env.sim.data.time),
            phase=self.phase,
            attempt=self.attempt,
            lateral_mm=float(lateral * 1000),
            tilt_deg=float(np.degrees(tilt)),
            yaw_err_deg=float(np.degrees(abs(self.rod_yaw()))),
            depth_mm=float(depth * 1000),
            grasped=self.servo.grasped(self.fgeoms),
            n_contacts=self.n_contacts(),
            eef_pos=self.servo.eef_pos.tolist(),
            tip_pos=tip.tolist(),
            capture_tol_mm=G.capture_tol(self.cavity) * 1000,
            seat_depth_mm=G.SEAT_DEPTH * 1000,
        )
        self.trace.append(t.to_dict())
        if self.viewer is not None and self.viewer.draw(t) is False:
            self._quit = True
            return False
        return self.servo.steps < self.max_steps

    def _set(self, phase):
        self.phase = phase

    # ------------------------------------------------------------ phases
    def _grasp_frame(self, flip=False):
        """Top-down grasp on the Ø25.4 mm ceramic grip, closing across the rod.

        `flip` negates the closing axis. The grip is a cylinder so both are the
        same physical grasp, but they put the wrist in mirrored configurations
        and one of them can drive joint 7 into its limit once the rod is stood
        upright, after which the arm cannot track the transport target at all.
        """
        p, R = self.frame_pose()
        grip_w = p + R @ G.GRIP_LOCAL
        rod = R @ G.ROD_AXIS_LOCAL
        closing = np.cross(np.array([0.0, 0.0, 1.0]), rod)
        if np.linalg.norm(closing) < 1e-6:
            return "upright_failed"
        closing /= np.linalg.norm(closing)
        if flip:
            closing = -closing
        Rg = grasp_mat(approach=[0, 0, -1.0], closing=closing)

        self._set("reach_grip")
        if not self.servo.move_to(grip_w + np.array([0, 0, APPROACH_UP]), Rg,
                                  grip=OPEN, pos_tol=3e-3, max_steps=220):
            return "ik_unreachable"
        self._set("descend_grip")
        self.servo.move_to(grip_w, Rg, grip=OPEN, pos_tol=3e-3, max_steps=160)
        self._set("close_gripper")
        self.servo.set_gripper(True)
        if not self.servo.grasped(self.fgeoms):
            return "grasp_missed"

        self._set("lift")
        self.servo.move_to(self.servo.eef_pos + np.array([0, 0, LIFT_CLEAR]), Rg,
                           grip=CLOSE, pos_tol=5e-3, max_steps=200)
        if not self.servo.grasped(self.fgeoms):
            return "grasp_slipped"
        self._Rg, self._closing = Rg, closing
        return None

    def _upright(self):
        """Rotate the rod from flat to vertical, tip down.

        The hand holds the rod rigidly, so standing the rod up means rotating the
        hand 90 deg about the closing axis. The sign is picked from the geometry
        rather than assumed: the wrong one leaves the tip pointing at the ceiling.
        """
        self._set("lift")
        self.servo.move_to(np.array([self.servo.eef_pos[0], self.servo.eef_pos[1], SAFE_Z]),
                           self._Rg, grip=CLOSE, pos_tol=6e-3, max_steps=220)

        self._set("upright")
        best = None
        for sign in (+1.0, -1.0):
            Rr = T.quat2mat(T.axisangle2quat(self._closing * (sign * np.pi / 2)))
            if (Rr @ self.rod_axis())[2] > 0:  # tip -> mount pointing up == tip down
                best = Rr
                break
        if best is None:
            return "upright_failed"
        self.servo.move_to(self.servo.eef_pos, best @ self._Rg, grip=CLOSE,
                           pos_tol=8e-3, rot_tol=0.05, max_steps=260)
        if not self.servo.grasped(self.fgeoms):
            return "dropped"
        # No abs(). The rod axis runs tip -> mount, so a POSITIVE z means the
        # mount is above the tip, which is the only way round that can be
        # inserted. Folding the sign away made an upside-down rod - tip in the
        # air, 180 deg from usable - measure as 0 deg of tilt and sail through
        # this gate. Downstream that is unrecoverable: the descent drives the
        # blunt mount end at the bore, and the straightening step then attempts
        # a half-turn of the wrist that throws the frame out of the fingers.
        if np.degrees(np.arccos(np.clip(self.rod_axis()[2], -1, 1))) > 12:
            return "upright_failed"
        self._Rhand = best @ self._Rg
        return None

    def _fix_yaw(self):
        """Spin the wrist about world z so the square rod matches the square hole.

        `_yaw_offset` adds a quarter turn per retry. The hole is square, so a
        90 deg offset is physically the same alignment, but it puts the arm in a
        different configuration - which is the only useful thing a retry can
        vary, since the reset is deterministic for a given seed.
        """
        for _ in range(3):
            err = self.rod_yaw() - self._yaw_offset
            # 2 deg, not 6. A square of side 7.5 mm turned by t occupies
            # 7.5*(cos t + sin t) across the bore: 7.76 mm at 2 deg, but 8.24 mm
            # at 6 deg, which eats 0.74 mm of a 2.5 mm total clearance before the
            # rod has been asked to be straight or centred as well.
            if abs(err) < np.radians(2):
                return None
            Rz = T.quat2mat(T.axisangle2quat(np.array([0.0, 0.0, -err])))
            self._Rhand = Rz @ self._Rhand
            self.servo.move_to(self.servo.eef_pos, self._Rhand, grip=CLOSE,
                               pos_tol=8e-3, rot_tol=0.04, max_steps=140)
            if self._quit:
                return "max_steps"
        return None

    def _straighten_rod(self, tol_deg=0.5, rounds=4, about_tip=False):
        """Rotate the hand until the rod actually hangs vertical.

        `_upright` sets the hand to the orientation that *should* stand the rod
        up, and then trusts it. That trust is misplaced: the grasp is on a
        25.4 mm cylinder, so the frame can pivot within the fingers, and the rod
        ends up at an angle the hand orientation does not describe. Measured on
        the widened placement ranges, one seed reached the mouth 9.5 deg off
        vertical - inside `_upright`'s own 12 deg gate, yet far too crooked for a
        1.25 mm clearance bore.

        So close the loop on the rod itself, the same way every other phase here
        closes the loop on the tip rather than on the hand.

        `tol_deg` is 0.5, and the reason is arithmetic rather than caution. A
        full seat is 130 mm, so the 7.5 mm square section ends up engaged over
        ~100 mm of a bore with 1.25 mm of clearance a side. The rod can be off
        vertical by at most atan(1.25/100) = 0.72 deg before the two ends of
        that engagement want to be on opposite walls. Measured tilt leaving the
        old 2 deg tolerance was 1.2-2.0 deg - two to three times the budget -
        and every jam sat just past the 30 mm mark where the square arrives.

        `about_tip` rotates around the rod tip instead of around the hand. The
        hand is 187 mm above the tip, so a 2 deg correction applied at the wrist
        sweeps the tip 6.5 mm sideways - more than the bore's 5 mm half-width.
        Straightening the rod while its tip is inside the bore is only possible
        by translating the hand along the arc that holds the tip still.
        """
        for _ in range(rounds):
            axis = self.rod_axis()
            up = np.array([0.0, 0.0, 1.0])
            cross = np.cross(axis, up)
            s = float(np.linalg.norm(cross))
            if s < 1e-9 or np.degrees(np.arcsin(min(1.0, s))) < tol_deg:
                return None
            angle = float(np.arctan2(s, float(np.dot(axis, up))))
            # Trim, never overhaul. A big correction here is not a tilt to be
            # nudged out - it means the rod is in the wrong orientation
            # entirely, which is `_upright`'s job to catch, and attempting it
            # anyway whips the frame out of the fingers: five of twenty seeds
            # newly reported "dropped" when this ran unbounded.
            if np.degrees(angle) > 25.0:
                return "upright_failed"
            angle = float(np.clip(angle, -np.radians(8.0), np.radians(8.0)))
            Rd = T.quat2mat(T.axisangle2quat(cross / s * angle))
            self._Rhand = Rd @ self._Rhand
            goal = self.servo.eef_pos
            if about_tip:
                tip = self.tip()
                goal = tip + Rd @ (self.servo.eef_pos - tip)
            # rot_tol has to be finer than the tilt being chased or the servo
            # stops while still outside it: 0.03 rad is 1.7 deg, so against a
            # 0.5 deg target it declared itself arrived before moving.
            self.servo.move_to(goal, self._Rhand, grip=CLOSE,
                               pos_tol=1e-3, rot_tol=0.008, max_steps=120)
            if not self.servo.grasped(self.fgeoms):
                return "dropped"
            if self._quit:
                return "max_steps"
        return None

    def _transport_and_align(self):
        """Servo until the tip sits inside the capture cone above the cavity mouth."""
        mouth = self.cavity.centre
        self._set("transport")
        hand = np.array([mouth[0], mouth[1], mouth[2] + GRIP_TO_TIP + HOVER])
        self.servo.move_to(hand, self._Rhand, grip=CLOSE, pos_tol=6e-3, max_steps=320)
        if not self.servo.grasped(self.fgeoms):
            return "dropped"

        bad = self._fix_yaw()
        if bad:
            return bad

        # Stand the rod up before asking the tip to hit a 1.25 mm target. A
        # crooked rod puts the tip somewhere the hand cannot reason about, and
        # the align loop below then spends its whole budget chasing a lateral
        # error that rotation, not translation, would have removed.
        bad = self._straighten_rod()
        if bad:
            return bad

        self._set("align")
        target_tip = np.array([mouth[0], mouth[1], mouth[2] + HOVER])
        for _ in range(60):
            err = target_tip - self.tip()
            if np.linalg.norm(err[:2]) < 0.6 * G.capture_tol(self.cavity) and abs(err[2]) < 4e-3:
                return None
            # correct the hand by the leftover tip error - this is what cancels
            # the grasp offset and the 187 mm lever arm
            self.servo.move_to(self.servo.eef_pos + err, self._Rhand, grip=CLOSE,
                               pos_tol=2e-3, max_steps=25)
            if self._quit:
                return "max_steps"
        return "align_timeout"

    def _insert(self):
        """Lower into the cavity, with a spiral search whenever descent stalls."""
        self._set("insert")
        mouth = self.cavity.centre
        stalls = 0
        last_depth = -1e9
        # Whether the previous iteration actually commanded a descent, so a
        # deliberate hold is not mistaken for a jam.
        descending = True
        # The rod is straightened once, up at hover height, and then not looked
        # at again for the whole 30 mm run down to where the square section
        # meets the bore - which is the only place the tilt actually matters.
        # Re-check it once on the way, while the cone is still the only thing
        # inside and there is room to rotate.
        squared_up = False
        radii = [0.0, 0.0015, 0.0025, 0.0035]
        for it in range(80):
            tip = self.tip()
            lateral, tilt, _, depth = G.insertion_error(
                tip, self.rod_axis(), self.rod_yaw(), self.cavity
            )
            if depth >= INSERT_DEPTH:
                return None
            if not self.servo.grasped(self.fgeoms):
                return "dropped"
            # Progress since the LAST iteration, not since the best depth ever
            # reached. The running maximum latches: the spiral search below
            # deliberately lifts 6 mm to free a jam, after which depth can never
            # again exceed the old high-water mark for many iterations, so every
            # subsequent step counts as a stall no matter how well the recovery
            # is going. Measured, that drove the rod from 44 mm in to 16 mm above
            # the mouth - each search lifting 6 mm while the descent won back 3 -
            # and then reported "jammed" for a rod that had already been deep
            # enough to seat.
            # Only a step that ASKED to go down can stall. The loop now holds
            # height on purpose while it centres the rod, and counting those
            # holds as stalls made the recovery search fire against a descent
            # that was working exactly as intended - it backed the rod out of
            # the bore to fix a jam that did not exist.
            # Last chance to fix the tilt: the cone is in, the square is not, so
            # the rod is still free to rotate but the tip is already lined up on
            # the hole and must stay there - hence about_tip.
            if not squared_up and depth > 0.018:
                squared_up = True
                if self._straighten_rod(tol_deg=0.5, rounds=3, about_tip=True):
                    return "upright_failed"
                continue

            if descending and depth <= last_depth + 3e-4:
                stalls += 1
            elif descending:
                stalls = 0
            last_depth = depth

            if stalls >= 4:
                # Before treating a stall as a jam, ask whether pushing is still
                # the right move at all. Past this depth the square section is
                # engaged in the bore, which holds the rod parallel - and from
                # there gravity finishes the insertion better than the arm can.
                # Gravity pulls exactly vertically, so it cannot lever the rod
                # the way a sideways hand correction does, and it does not need
                # the compliant grasp to transmit anything. Every jam recorded
                # sat at 44 mm, already past this line, being shoved by an arm
                # that had no way to push straight.
                # The lateral and 4 deg conditions here were blocking exactly the
                # runs that needed this. Measured on the six jamming seeds, all
                # of them past 40 mm: opening the fingers and holding still let
                # every one slide further down on its own, by 7 to 29 mm - so a
                # jam is not a wedge, it is the arm being unable to push straight
                # through its own compliant grasp. The gain tracked the tilt
                # (2.4 deg -> +22 mm, 6.4 deg -> +7 mm), which is why the tilt
                # work above comes first: gravity finishes the job, but only from
                # a rod that is already close to vertical.
                # `lateral` is read at the 2 mm cone tip, 100 mm below the square
                # that is doing the binding, so it says little once this deep.
                if depth >= GRAVITY_FINISH_DEPTH and tilt < np.radians(8.0):
                    return None
                # jammed on the lip: back off, offset laterally, come back down
                self._set("search")
                r = radii[min(len(radii) - 1, stalls // 4)]
                ang = 1.7 * it
                off = np.array([r * np.cos(ang), r * np.sin(ang), 0.0])
                self.servo.move_to(self.servo.eef_pos + np.array([0, 0, 0.006]),
                                   self._Rhand, grip=CLOSE, pos_tol=2e-3, max_steps=25)
                lat = mouth[:2] + off[:2] - self.tip()[:2]
                self.servo.move_to(self.servo.eef_pos + np.array([lat[0], lat[1], 0.0]),
                                   self._Rhand, grip=CLOSE, pos_tol=1.5e-3, max_steps=25)
                self._set("insert")
                if stalls > 24:
                    return None if depth >= GRAVITY_FINISH_DEPTH else "jammed"

            # Which lateral correction is safe depends on whether the tip is
            # still free. Once it is inside the bore the tip cannot move, so
            # translating the hand to "fix" its lateral error does not move the
            # tip at all - it levers the rod over about the tip, and the tilt
            # that creates makes the lateral reading worse, which asks for more
            # sideways motion. Measured across the widened placement ranges: the
            # rod entered at 0.2-3.5 deg off vertical and left at 15-17 deg,
            # while the one seed that seated stayed at 4.0. `is_seated` requires
            # under 8 deg, so that runaway is the jam.
            # The rod is not one shape, and the two shapes need opposite tactics.
            # Measured profile, from the tip: a cone over the first ~31 mm, then
            # a 6.5 mm cylinder, then the 7.5 mm SQUARE section. The bore is
            # 10 mm square. So the cone drops in on a 4 mm tolerance while the
            # square section has only 1.25 mm a side - and every jam recorded
            # sat at 44.4 mm depth with 2.1-2.5 mm of lateral error, which is
            # exactly the square arriving off-centre and wedging.
            # Centring only. There is no correcting tilt by sliding the hand in
            # this task, at any depth, and the arithmetic says why: translating
            # the hand rotates the rod only while the tip is pinned, and the tip
            # is never pinned here. The cone tops out at 3.25 mm of radius in a
            # bore of 5 mm half-width, so it would need 4.7 mm to touch a wall
            # and never gets there; nothing grips the rod until the 3.75 mm
            # square section arrives at ~30 mm, and by then the bore holds it
            # parallel anyway.
            #
            # So the term only ever translated the rod off-axis while leaving
            # the tilt exactly as it found it. Measured, it drove lateral 0.39 ->
            # 2.24 mm on the approach; removing it above the mouth held lateral
            # at 0.05-0.28 mm, and it then reappeared as 0.28 -> 2.13 mm the
            # moment depth went positive and the term switched back on. Tilt
            # belongs to `_straighten_rod`, which rotates the wrist.
            centre = np.clip((mouth[:2] - self.tip()[:2]) * 0.6, -8e-4, 8e-4)
            lat = centre
            # Do not commit the square section until the rod is centred enough
            # for it to fit. 1.0 mm leaves a quarter of the 1.25 mm clearance in
            # hand; without this gate the descent carries a 2.5 mm error into a
            # slot that cannot take it, and no amount of pushing afterwards
            # recovers.
            crooked = tilt > np.radians(6.0)
            off_centre = lateral > SQUARE_LATERAL_OK and depth > SQUARE_FROM_TIP - 0.008
            descending = not (crooked or off_centre)
            step = np.array([lat[0], lat[1], -0.003 if descending else 0.0])
            # The servo's arrival tolerance has to be finer than the correction
            # being asked for, or the correction is a no-op. This was 1.5 mm
            # while the capped lateral term is at most 0.8 mm, so `move_to`
            # judged itself already arrived and never moved the hand: lateral
            # sat at ~1.5 mm - the tolerance itself - and the square section
            # could never be centred enough to enter a slot with 1.25 mm a side.
            # The 3 mm descent step cleared the old tolerance, which is why only
            # the centring silently stopped working.
            self.servo.move_to(self.servo.eef_pos + step, self._Rhand, grip=CLOSE,
                               pos_tol=4e-4, max_steps=22)
            if self._quit:
                return "max_steps"
        # Out of iterations rather than stalled, but the same choice applies: if
        # the rod is deep enough for gravity to have a chance, hand over to it
        # instead of reporting a failure the release step could still undo.
        _, _, _, depth = G.insertion_error(
            self.tip(), self.rod_axis(), self.rod_yaw(), self.cavity
        )
        return None if depth >= GRAVITY_FINISH_DEPTH else "jammed"

    def _release(self):
        """Open, let the rod drop free, and only then pull the hand away.

        Retreating too early drags the rod back out: the fingers still brush the
        grip cylinder, and the hand is 187 mm above the tip so any lift is fully
        transmitted. Holding still until it has slid down decouples the two.
        """
        self._set("release")
        self.servo.set_gripper(False, n=26)
        # Gravity finishes the insertion, and now it has real work to do. The
        # descent may hand over at 44 mm with ~100 mm of bore still to travel,
        # so 60 steps - three seconds - is no longer a formality. The hand must
        # not move for any of it: retreating while the rod is still sliding
        # takes the rod with it.
        # Wait for the rod to stop sliding, not for a fixed count. The 200 was
        # sized for the worst case - a handover at 44 mm with 100 mm still to
        # travel - and every episode paid it in full even when the rod had
        # already bottomed out.
        still, last = 0, self.tip()
        for _ in range(200):
            self.servo.act(grip=OPEN)
            if self.servo.aborted:
                break
            here = self.tip()
            if float(np.linalg.norm(here - last)) < 2e-4:
                still += 1
                if still >= 25:
                    break
            else:
                still = 0
            last = here
        self._set("retreat")
        # Three separate moves, never combined. Retreating straight up drags the
        # rod back out (0/15 seeds), and rising while moving sideways is the same
        # mistake in disguise: it sweeps the fingers across the grip cylinder
        # while still pulling upward.
        #   1. sink a little, so any remaining finger contact can only push down
        #      on a rod that is already bottomed out, never lift it
        self.servo.move_to(self.servo.eef_pos + np.array([0, 0, -RELEASE_SINK]),
                           self._Rhand, grip=OPEN, pos_tol=4e-3, max_steps=90)
        #   2. clear sideways at that lower height, purely lateral
        away = self._Rhand[:, 2] * -0.06
        self.servo.move_to(self.servo.eef_pos + np.array([away[0], away[1], 0.0]),
                           self._Rhand, grip=OPEN, pos_tol=8e-3, max_steps=140)
        #   3. only now go up, well clear of the rod
        self.servo.move_to(self.servo.eef_pos + np.array([0, 0, 0.14]), self._Rhand,
                           grip=OPEN, pos_tol=1e-2, max_steps=160)
        self.servo.hold(40, grip=OPEN)
        self._set("done")

    # ------------------------------------------------------------ entry
    def run(self, seed):
        from .env_setup import reset_and_settle
        t0 = time.time()
        failure = failed_phase = None
        for attempt in range(1, self.max_attempts + 1):
            self.attempt = attempt
            self.servo.steps = 0
            self.servo.aborted = False
            self._quit = False
            reset_and_settle(self.env, seed=seed)
            self.cavity = G.read_cavity(self.env)
            # Each attempt starts a fresh recording. A retry replays the episode
            # from the same reset, so keeping the failed attempt's steps would
            # splice a doomed trajectory onto the successful one.
            self.record = [] if self.collect else None
            if self.collect:
                # captured per attempt: the placement sampler is re-rolled on
                # every reset, so the XML that goes with these states is this one
                self.model_xml = self.env.sim.model.get_xml()
            self.servo = Servo(self.env, on_step=self._on_step, record=self.record)

            # The flipped grasp is the one that keeps joint 7 off its limit for
            # these poses, so it is not something to alternate away from; retries
            # vary the quarter turn instead.
            self._yaw_offset = (attempt - 1) * (np.pi / 2)
            failure = (self._grasp_frame(flip=True) or self._upright()
                       or self._transport_and_align() or self._insert())
            failed_phase = self.phase
            if failure is None:
                self._release()
                if G.is_seated(self.tip(), self.rod_axis(), self.cavity):
                    break
                # the insert itself worked, so the rod came back out on release
                failure, failed_phase = "dropped", "release"
            if self._quit:
                break

        tip, axis = self.tip(), self.rod_axis()
        lateral, _, _, depth = G.insertion_error(tip, axis, self.rod_yaw(), self.cavity)
        seated = G.is_seated(tip, axis, self.cavity)
        return EpisodeResult(
            seed=seed,
            success=bool(seated),
            env_predicate=bool(self.env._check_frame_assembled()),
            failure=None if seated else (failure or "max_steps"),
            failed_phase=None if seated else failed_phase,
            steps=self.servo.steps,
            wall_time_s=time.time() - t0,
            attempts=self.attempt,
            final_lateral_mm=float(lateral * 1000),
            final_depth_mm=float(depth * 1000),
            trace=self.trace,
        )