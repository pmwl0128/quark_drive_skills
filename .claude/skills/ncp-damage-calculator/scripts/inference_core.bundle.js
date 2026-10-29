var QuarkInferenceCore = (function(exports) {
	Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
	//#region src/pages/calc/tune/model.ts
	/** Special moves that the engine resolves against Def: the SP that moves their cliff is Def. */
	var PHYS_DEF_SPECIAL_MOVES = /* @__PURE__ */ new Set([
		"Psyshock",
		"Psystrike",
		"Secret Sword"
	]);
	/** The defensive stat a damaging move presses on, or null for a status move. */
	function pressedStat(move, category) {
		if (category === "Physical") return "def";
		if (category === "Special") return PHYS_DEF_SPECIAL_MOVES.has(move) ? "def" : "spd";
		return null;
	}
	//#endregion
	//#region src/pages/calc/infer/move_readability.ts
	/** Move eligibility shared by the web inference UI and the local inference model. */
	var NOT_READABLE = /* @__PURE__ */ new Set([
		"Super Fang",
		"Ruination",
		"Nature's Madness",
		"Final Gambit",
		"Endeavor",
		"Counter",
		"Mirror Coat",
		"Metal Burst",
		"Comeuppance",
		"Bide",
		"Brine",
		"Wring Out",
		"Crush Grip",
		"Hard Press",
		"Eruption",
		"Water Spout",
		"Dragon Energy",
		"Flail",
		"Reversal",
		"Gyro Ball",
		"Electro Ball",
		"Rollout",
		"Ice Ball",
		"Fury Cutter",
		"Echoed Voice"
	]);
	var NOT_INTO_FOE = /* @__PURE__ */ new Set(["Foul Play"]);
	var NOT_FROM_FOE = /* @__PURE__ */ new Set(["Body Press"]);
	/** The factorised model cannot use a hit whose damage changes with an additional hidden axis.
	* Reject the complete observation rather than silently dropping the affected ability candidate. */
	function frameIssue(input) {
		if (input.kind === "offense" && input.foeCurrentHP != null && input.foeAbilities.some((a) => [
			"Blaze",
			"Torrent",
			"Overgrow",
			"Swarm"
		].includes(a))) {
			const low = input.foeBaseHP === 1 ? 1 : input.foeBaseHP + 75;
			const high = input.foeBaseHP === 1 ? 1 : low + 32;
			if (input.foeCurrentHP > Math.floor(low / 3) && input.foeCurrentHP <= Math.floor(high / 3)) return "hp_trigger_hidden_hp";
		}
		return null;
	}
	function bulkUse(move, category) {
		if (!move || !category || category === "Status") return { reason: "status" };
		if (NOT_READABLE.has(move) || NOT_INTO_FOE.has(move)) return { reason: "unreadable" };
		const key = pressedStat(move, category);
		return key ? { key } : { reason: "status" };
	}
	function offenseUse(move, category) {
		if (!move || !category || category === "Status") return { reason: "status" };
		if (NOT_READABLE.has(move) || NOT_FROM_FOE.has(move)) return { reason: "unreadable" };
		return { key: category === "Physical" ? "atk" : "spa" };
	}
	//#endregion
	//#region src/pages/calc/infer/survival.ts
	/** Censor a full-HP hit at one HP, consistently in the web and local inference adapters. */
	function survivesHitAtOne(attackerAbility, attackerAbilityOn, defenderItem, defenderAbility, defenderAbilityOn, move) {
		if (defenderItem === "Focus Sash" && defenderAbility !== "Klutz") return true;
		const bypass = attackerAbilityOn !== false && [
			"Mold Breaker",
			"Teravolt",
			"Turboblaze"
		].includes(attackerAbility) || [
			"Sunsteel Strike",
			"Moongeist Beam",
			"Photon Geyser"
		].includes(move);
		return defenderAbility === "Sturdy" && defenderAbilityOn !== false && !bypass;
	}
	//#endregion
	//#region src/pages/calc/infer/speed_projection.ts
	function projectSpeeds(plan, speeds) {
		const speed = (index) => index === null ? null : speeds[index] ?? null;
		const ranges = /* @__PURE__ */ new Map();
		let incomplete = plan.mine !== null && speed(plan.mine) === null;
		for (const probe of plan.probes) {
			const lo = speed(probe.lo), hi = speed(probe.hi);
			if (lo === null || hi === null) {
				incomplete = true;
				continue;
			}
			const previous = ranges.get(probe.nature);
			ranges.set(probe.nature, {
				nature: probe.nature,
				lo: Math.min(lo, previous?.lo ?? lo),
				hi: Math.max(hi, previous?.hi ?? hi),
				spHi: Math.max(probe.spHi, previous?.spHi ?? 0)
			});
		}
		return {
			mine: speed(plan.mine),
			ranges: [...ranges.values()],
			incomplete
		};
	}
	//#endregion
	//#region src/lib/stats.ts
	var LEVEL = 50;
	function hpAt(base, sp) {
		if (base === 1) return 1;
		return Math.floor((2 * base + 31) * LEVEL / 100) + LEVEL + 10 + sp;
	}
	function statAt(base, sp, natureMult) {
		const raw = Math.floor((2 * base + 31) * LEVEL / 100) + 5 + sp;
		return Math.floor(raw * natureMult);
	}
	/** One stat's actual Lv.50 value from base + SP + nature multiplier (HP ignores nature). Used by
	* the bulk / attack indices (HP×Def, Atk×BP…) that need a single resolved stat, not the range. */
	function actualStat(base, key, sp, natureMult) {
		return key === "hp" ? hpAt(base, sp) : statAt(base, sp, natureMult);
	}
	//#endregion
	//#region src/pages/calc/infer/model.ts
	/** Reverse inference of an opponent's build from damage actually seen in a battle (配置反推).
	*
	* The opponent's build is a hidden state: a nature, an item and an ability out of what the
	* environment actually plays (usage >= 1%), and HP / Atk / Def / SpA / SpD SP anywhere in 0..32 with
	* the 66-point budget. Every observation is a filter over that state:
	*
	* - our move into them shows their HP as a percentage (floored; alive is never below 1%), so it
	*   constrains HP x Def (or SpD);
	* - their move into us shows our HP exactly, so it constrains their Atk (or SpA) with item and ability.
	*
	* Damage never depends on the defender's HP SP, so a move's rolls are computed once per defensive
	* stat value and the HP axis is pure arithmetic — the trick the `tune` operator uses for its HP lane.
	* A state survives when at least one of the sixteen rolls reproduces every observation; its weight is
	* the product of the fraction of rolls that do, times the environment's share of its nature, item
	* and ability. The views read the surviving set; weights shade it and give relative nature shares,
	* not a confirmation of the opponent's true nature.
	*
	* Pure: the engine is reached through the `RollsAt` callbacks, and `undefined` from one means "not
	* computed yet", which makes the whole answer pending rather than wrong. */
	var W = 33;
	var SP_RANGE = Array.from({ length: W }, (_, sp) => sp);
	var BULK_KEYS = ["def", "spd"];
	var OFF_KEYS = ["atk", "spa"];
	/** The opponent's HP as the game prints it: floored percent, but a Pokémon still standing never
	* reads below 1%. */
	function shownPercent(current, max) {
		if (current <= 0) return 0;
		return Math.max(1, Math.floor(100 * current / max));
	}
	var ceilDiv = (a, b) => Math.floor((a + b - 1) / b);
	/** The integer HP values that print as `p`%, or null when no value of this maximum can. 100% is
	* full HP only; 0% is fainted. */
	function hpShownAs(p, max) {
		if (!Number.isInteger(p) || p < 0 || p > 100 || max <= 0) return null;
		if (p === 0) return [0, 0];
		if (p === 100) return [max, max];
		const lo = p <= 1 ? 1 : ceilDiv(p * max, 100);
		const hi = Math.min(max - 1, ceilDiv((p + 1) * max, 100) - 1);
		return lo <= hi ? [lo, hi] : null;
	}
	/** Our move into them: does a hit of `damage` take a Pokémon of `max` HP from `before`% to `after`%? */
	function foeRollFits(damage, max, before, after, stopAtOne = false) {
		const range = hpShownAs(before, max);
		if (!range) return false;
		for (let current = range[0]; current <= range[1]; current++) if (shownPercent(stopAtOne && current === max ? Math.max(1, current - damage) : current - damage, max) === after) return true;
		return false;
	}
	/** Their move into us: our HP is exact, so the hit is either exactly the difference or, on a faint,
	* at least what was left. */
	function mineRollFits(damage, before, after, stopAtOne = false) {
		return stopAtOne ? Math.max(1, before - damage) === after : after > 0 ? before - damage === after : damage >= before;
	}
	function share(rolls, fits) {
		if (!rolls.length) return 0;
		let hit = 0;
		for (const roll of rolls) if (fits(roll)) hit++;
		return hit / rolls.length;
	}
	var Pending = class extends Error {};
	var PENDING = new Pending();
	function need(value) {
		if (value === void 0) throw PENDING;
		return value;
	}
	/** HP and stat values for every SP position, per nature multiplier. */
	function statTables(base) {
		const hp = SP_RANGE.map((sp) => actualStat(base.hp, "hp", sp, 1));
		const stat = (key, mult) => SP_RANGE.map((sp) => actualStat(base[key], key, sp, mult));
		const table = {};
		for (const key of [...BULK_KEYS, ...OFF_KEYS]) table[key] = {
			"0.9": stat(key, .9),
			"1": stat(key, 1),
			"1.1": stat(key, 1.1)
		};
		return {
			hp,
			stat: (key, mult) => table[key][String(mult)]
		};
	}
	/** Share of rolls reproducing one bulk observation, over (HP SP, stat SP): index hp * 33 + sp. */
	function bulkShares(evidence, item, ability, mult, hp) {
		const out = new Float64Array(W * W);
		for (const sp of SP_RANGE) {
			const rolls = need(evidence.rolls(ability, mult, sp, item));
			if (!rolls) continue;
			for (let h = 0; h < W; h++) out[h * W + sp] = share(rolls, (damage) => foeRollFits(damage, hp[h], evidence.before, evidence.after, evidence.stopAtOne?.(item, ability)));
		}
		return out;
	}
	function offShares(evidence, item, ability, mult) {
		const out = new Float64Array(W);
		for (const sp of SP_RANGE) {
			const rolls = need(evidence.rolls(item, ability, mult, sp));
			out[sp] = rolls ? share(rolls, (damage) => mineRollFits(damage, evidence.before, evidence.after, evidence.stopAtOne?.(item, ability))) : 0;
		}
		return out;
	}
	var RULER_BINS = 48;
	/** The best HP x stat product reachable with `total` SP between the two, neutral nature. */
	function bestProduct(hp, stat, total) {
		let best = 0;
		for (let h = Math.max(0, total - 32); h <= Math.min(32, total); h++) best = Math.max(best, hp[h] * stat[total - h]);
		return best;
	}
	/** Bulk-equivalent SP: the fewest neutral-nature SP in HP + the stat that reach this product. Below
	* 0 SP (a lowering nature) is reported as 0; above 64 as 64. */
	function bulkEquivalent(product, hp, stat) {
		for (let total = 0; total <= 64; total++) if (bestProduct(hp, stat, total) >= product) return total;
		return 64;
	}
	function rulerAxis(space, key) {
		const { hp, stat } = statTables(space.base);
		return [hp[0] * stat(key, .9)[0], hp[32] * stat(key, 1.1)[32]];
	}
	function prepare(space, evidence) {
		const { hp, stat } = statTables(space.base);
		const observed = {
			hp: false,
			atk: false,
			def: false,
			spa: false,
			spd: false
		};
		const multsOf = (key) => [...new Set(space.natures.map((n) => n.mult[key]))];
		const bulkL = {
			def: /* @__PURE__ */ new Map(),
			spd: /* @__PURE__ */ new Map()
		};
		for (const key of BULK_KEYS) {
			const list = evidence.filter((e) => e.kind === "bulk" && e.key === key);
			if (list.length) {
				observed[key] = true;
				observed.hp = true;
			}
			for (const { name: item } of space.items) for (const { name: ability } of space.abilities) for (const mult of multsOf(key)) {
				let product = null;
				for (const e of list) {
					const shares = bulkShares(e, item, ability, mult, hp);
					if (!product) product = shares;
					else for (let at = 0; at < product.length; at++) product[at] *= shares[at];
				}
				bulkL[key].set(`${item}|${ability}|${mult}`, product);
			}
		}
		const offL = {
			atk: /* @__PURE__ */ new Map(),
			spa: /* @__PURE__ */ new Map()
		};
		for (const key of OFF_KEYS) {
			const list = evidence.filter((e) => e.kind === "offense" && e.key === key);
			if (list.length) observed[key] = true;
			for (const { name: item } of space.items) for (const { name: ability } of space.abilities) for (const mult of multsOf(key)) {
				let product = null;
				for (const e of list) {
					const shares = offShares(e, item, ability, mult);
					if (!product) product = shares;
					else for (let at = 0; at < W; at++) product[at] *= shares[at];
				}
				offL[key].set(`${item}|${ability}|${mult}`, product);
			}
		}
		return {
			hp,
			stat,
			bulkL,
			offL,
			observed
		};
	}
	var ONES2 = new Float64Array(W * W).fill(1);
	var ONES1 = new Float64Array(W).fill(1);
	/** Run the inference, or null while any roll it needs is still being computed. `countOnly` skips
	* every view and returns just the surviving-state count (for the narrowing history). */
	function infer(space, evidence, countOnly = false, preferred, withObservationWeights = false) {
		let prep;
		try {
			prep = prepare(space, evidence);
		} catch (error) {
			if (error === PENDING) return null;
			throw error;
		}
		const { hp, stat, bulkL, offL, observed } = prep;
		const N = space.natures.length;
		const I = space.items.length;
		const A = space.abilities.length;
		const natureOk = new Array(N).fill(false);
		const natureMass = new Float64Array(N);
		const itemOk = new Array(I).fill(false);
		const abilityOk = new Array(A).fill(false);
		const mass = {
			hp: new Float64Array(W),
			atk: new Float64Array(W),
			def: new Float64Array(W),
			spa: new Float64Array(W),
			spd: new Float64Array(W),
			spe: new Float64Array(W)
		};
		const axes = {
			def: rulerAxis(space, "def"),
			spd: rulerAxis(space, "spd")
		};
		const bins = {
			def: new Float64Array(RULER_BINS),
			spd: new Float64Array(RULER_BINS)
		};
		const band = {
			def: [Infinity, -Infinity],
			spd: [Infinity, -Infinity]
		};
		const bulkProj = {
			def: [],
			spd: []
		};
		const offProj = {
			atk: [],
			spa: []
		};
		const observationWeights = withObservationWeights && !countOnly ? {
			bulk: {
				def: [],
				spd: []
			},
			offense: {
				atk: [],
				spa: []
			}
		} : void 0;
		const speedCaps = new Array(N * I * A).fill(-1);
		const [loDef, hiDef] = axes.def;
		const [loSpd, hiSpd] = axes.spd;
		const spanDef = hiDef - loDef;
		const spanSpd = hiSpd - loSpd;
		const binDef = (product) => Math.max(0, Math.min(RULER_BINS - 1, Math.floor((product - loDef) / spanDef * RULER_BINS)));
		const binSpd = (product) => Math.max(0, Math.min(RULER_BINS - 1, Math.floor((product - loSpd) / spanSpd * RULER_BINS)));
		let minDef = Infinity, maxDef = -Infinity, minSpd = Infinity, maxSpd = -Infinity;
		let count = 0;
		let mostLikely = null;
		let bestWeight = -1, bestDistance = Infinity;
		const distance = (key, sp) => Math.abs(sp - (preferred?.sps[key] ?? 0));
		const prefix = (values) => {
			const out = /* @__PURE__ */ new Float64Array(67);
			let sum = 0;
			for (let t = 0; t <= 66; t++) {
				sum += values[t] ?? 0;
				out[t] = sum;
			}
			return out;
		};
		for (let n = 0; n < N; n++) {
			const nature = space.natures[n];
			const statDef = stat("def", nature.mult.def), statSpd = stat("spd", nature.mult.spd);
			for (let i = 0; i < I; i++) {
				const item = space.items[i];
				for (let a = 0; a < A; a++) {
					const ability = space.abilities[a];
					const index = (n * I + i) * A + a;
					const lp = bulkL.def.get(`${item.name}|${ability.name}|${nature.mult.def}`) ?? ONES2;
					const ls = bulkL.spd.get(`${item.name}|${ability.name}|${nature.mult.spd}`) ?? ONES2;
					const la = offL.atk.get(`${item.name}|${ability.name}|${nature.mult.atk}`) ?? ONES1;
					const ly = offL.spa.get(`${item.name}|${ability.name}|${nature.mult.spa}`) ?? ONES1;
					const prior = nature.prior * item.prior * ability.prior;
					const projDef = new Uint8Array(W * W), projSpd = new Uint8Array(W * W);
					const projAtk = new Uint8Array(W), projSpa = new Uint8Array(W);
					bulkProj.def[index] = projDef;
					bulkProj.spd[index] = projSpd;
					offProj.atk[index] = projAtk;
					offProj.spa[index] = projSpa;
					const weightedDef = observationWeights ? new Float64Array(W * W) : null;
					const weightedSpd = observationWeights ? new Float64Array(W * W) : null;
					const weightedAtk = observationWeights ? new Float64Array(W) : null;
					const weightedSpa = observationWeights ? new Float64Array(W) : null;
					if (observationWeights) {
						observationWeights.bulk.def[index] = weightedDef;
						observationWeights.bulk.spd[index] = weightedSpd;
						observationWeights.offense.atk[index] = weightedAtk;
						observationWeights.offense.spa[index] = weightedSpa;
					}
					const offCount = /* @__PURE__ */ new Float64Array(67), offMass = /* @__PURE__ */ new Float64Array(67);
					const offBest = new Array(67).fill(null);
					for (let x = 0; x < W; x++) for (let y = 0; y < W; y++) {
						const weight = la[x] * ly[y];
						if (weight) {
							offCount[x + y]++;
							offMass[x + y] += weight;
						}
						if (weight && !countOnly) {
							const cost = distance("atk", x) + distance("spa", y), previous = offBest[x + y];
							if (!previous || weight > previous.weight || weight === previous.weight && cost < previous.distance) offBest[x + y] = {
								weight,
								distance: cost,
								atk: x,
								spa: y
							};
						}
					}
					if (!countOnly) for (let room = 1; room <= 66; room++) {
						const lower = offBest[room - 1], current = offBest[room];
						if (lower && (!current || lower.weight > current.weight || lower.weight === current.weight && lower.distance < current.distance)) offBest[room] = lower;
					}
					const offCounts = prefix(offCount), offWeights = prefix(offMass);
					const bulkCount = /* @__PURE__ */ new Float64Array(67), bulkMass = /* @__PURE__ */ new Float64Array(67);
					for (let h = 0; h < W; h++) for (let d = 0; d < W; d++) {
						const wd = lp[h * W + d];
						if (!wd) continue;
						for (let b = 0; b < W && h + d + b <= 66; b++) {
							const ws = ls[h * W + b];
							if (!ws) continue;
							const total = h + d + b, room = 66 - total;
							bulkCount[total]++;
							bulkMass[total] += wd * ws;
							if (!offCounts[room]) continue;
							count += offCounts[room];
							natureOk[n] = itemOk[i] = abilityOk[a] = true;
							if (countOnly) continue;
							const weight = prior * wd * ws * offWeights[room];
							if (weightedDef && weightedSpd) {
								weightedDef[h * W + d] += weight;
								weightedSpd[h * W + b] += weight;
							}
							natureMass[n] += weight;
							const choice = offBest[room];
							const stateWeight = prior * wd * ws * choice.weight;
							const cost = distance("hp", h) + distance("def", d) + distance("spd", b) + choice.distance;
							if (stateWeight > bestWeight || stateWeight === bestWeight && cost < bestDistance) {
								bestWeight = stateWeight;
								bestDistance = cost;
								mostLikely = {
									nature: nature.name,
									item: item.name,
									ability: ability.name,
									sps: {
										hp: h,
										def: d,
										spd: b,
										atk: choice.atk,
										spa: choice.spa,
										spe: Math.min(32, preferred?.sps.spe ?? 0, room - choice.atk - choice.spa)
									}
								};
							}
							projDef[h * W + d] = 1;
							projSpd[h * W + b] = 1;
							mass.hp[h] += weight;
							mass.def[d] += weight;
							mass.spd[b] += weight;
							const pd = hp[h] * statDef[d], ps = hp[h] * statSpd[b];
							bins.def[binDef(pd)] += weight;
							bins.spd[binSpd(ps)] += weight;
							minDef = Math.min(minDef, pd);
							maxDef = Math.max(maxDef, pd);
							minSpd = Math.min(minSpd, ps);
							maxSpd = Math.max(maxSpd, ps);
						}
					}
					if (countOnly) continue;
					const bulkCounts = prefix(bulkCount), bulkWeights = prefix(bulkMass);
					for (let spe = 0; spe < W; spe++) {
						const room = 66 - spe;
						let possible = false, weight = 0;
						for (let total = 0; total <= room; total++) {
							if (bulkCount[total] && offCounts[room - total]) possible = true;
							weight += bulkMass[total] * offWeights[room - total];
						}
						if (possible) speedCaps[index] = spe;
						mass.spe[spe] += prior * weight;
					}
					for (let x = 0; x < W; x++) for (let y = 0; y < W; y++) {
						const room = 66 - x - y;
						if (!la[x] || !ly[y] || !bulkCounts[room]) continue;
						const weight = prior * la[x] * ly[y] * bulkWeights[room];
						if (weightedAtk && weightedSpa) {
							weightedAtk[x] += weight;
							weightedSpa[y] += weight;
						}
						projAtk[x] = 1;
						projSpa[y] = 1;
						mass.atk[x] += weight;
						mass.spa[y] += weight;
					}
				}
			}
		}
		band.def = [minDef, maxDef];
		band.spd = [minSpd, maxSpd];
		const statView = (key) => {
			const row = Array.from(mass[key]);
			const top = Math.max(...row);
			const lo = row.findIndex((value) => value > 0);
			let hi = -1;
			for (let at = W - 1; at >= 0; at--) if (row[at] > 0) {
				hi = at;
				break;
			}
			return {
				observed: key === "spe" ? evidence.length > 0 : observed[key],
				lo: lo < 0 ? 1 : lo,
				hi,
				mass: row.map((value) => top > 0 ? value / top : 0)
			};
		};
		const ruler = (key) => {
			const top = Math.max(...bins[key]);
			const found = band[key][0] <= band[key][1];
			const equivalentOf = (product) => bulkEquivalent(product, hp, stat(key, 1));
			return {
				observed: observed[key],
				axis: axes[key],
				band: found ? [band[key][0], band[key][1]] : null,
				bins: Array.from(bins[key], (value) => top > 0 ? value / top : 0),
				equivalent: found ? [equivalentOf(band[key][0]), equivalentOf(band[key][1])] : null
			};
		};
		const lanes = (key) => {
			if (!observed[key]) return [];
			const mults = [...new Set(space.natures.map((nature) => nature.mult[key]))].sort((x, y) => y - x);
			const groups = /* @__PURE__ */ new Map();
			space.items.forEach((item, i) => {
				const rows = mults.map((mult) => {
					const flags = new Array(W).fill(false);
					space.natures.forEach((nature, n) => {
						if (nature.mult[key] !== mult) return;
						for (let a = 0; a < A; a++) {
							const proj = offProj[key][(n * I + i) * A + a];
							for (let sp = 0; sp < W; sp++) if (proj[sp]) flags[sp] = true;
						}
					});
					return {
						mult,
						flags
					};
				});
				const signature = JSON.stringify(rows);
				const lane = groups.get(signature);
				if (lane) lane.items.push(item.name);
				else groups.set(signature, {
					items: [item.name],
					rows,
					feasible: rows.some((row) => row.flags.some(Boolean))
				});
			});
			return [...groups.values()].sort((x, y) => Number(y.feasible) - Number(x.feasible));
		};
		const totalNatureMass = natureMass.reduce((sum, value) => sum + value, 0);
		return {
			count,
			natures: natureOk,
			natureShares: totalNatureMass > 0 ? Array.from(natureMass, (value) => value / totalNatureMass) : null,
			mostLikely,
			items: itemOk,
			abilities: abilityOk,
			stats: {
				hp: statView("hp"),
				atk: statView("atk"),
				def: statView("def"),
				spa: statView("spa"),
				spd: statView("spd"),
				spe: statView("spe")
			},
			speedCaps,
			rulers: {
				def: ruler("def"),
				spd: ruler("spd")
			},
			lanes: {
				atk: lanes("atk"),
				spa: lanes("spa")
			},
			bulkProj,
			offProj,
			...observationWeights ? { observationWeights } : {}
		};
	}
	/** Every "after" reading one observation could have shown, over the whole space: our HP left, or
	* their displayed percent. Null while pending. Used to explain readings outside this model. */
	function possibleAfters(space, evidence) {
		const { hp } = statTables(space.base);
		const out = /* @__PURE__ */ new Set();
		const mults = [...new Set(space.natures.map((nature) => nature.mult[evidence.key]))];
		try {
			for (const { name: ability } of space.abilities) for (const mult of mults) for (const sp of SP_RANGE) if (evidence.kind === "offense") for (const { name: item } of space.items) for (const damage of need(evidence.rolls(item, ability, mult, sp)) ?? []) out.add(Math.max(evidence.stopAtOne?.(item, ability) ? 1 : 0, evidence.before - damage));
			else for (const { name: item } of space.items) {
				const rolls = need(evidence.rolls(ability, mult, sp, item)) ?? [];
				const stop = evidence.stopAtOne?.(item, ability);
				for (let h = 0; h < W; h++) {
					const range = hpShownAs(evidence.before, hp[h]);
					if (!range) continue;
					for (const damage of rolls) for (let current = range[0]; current <= range[1]; current++) out.add(shownPercent(stop && current === hp[h] ? Math.max(1, current - damage) : current - damage, hp[h]));
				}
			}
		} catch (error) {
			if (error === PENDING) return null;
			throw error;
		}
		return [...out].sort((x, y) => x - y);
	}
	/** The possible readings nearest to `value`: the closest below and the closest above. */
	function nearestAfters(values, value) {
		return [values.filter((candidate) => candidate < value).pop(), values.find((candidate) => candidate > value)].filter((candidate) => candidate !== void 0);
	}
	/** Apply the same per-hit validity rule before intersecting observations. An individually impossible
	* reading is excluded; two individually possible readings may still form a real conflict. */
	function evaluateEvidence(space, records, preferred, withObservationWeights = false) {
		const impossible = /* @__PURE__ */ new Map(), skipped = /* @__PURE__ */ new Set();
		const evidence = [], accepted = [];
		let pending = false;
		for (const { id, evidence: hit } of records) {
			const single = infer(space, [hit], true);
			if (!single) pending = true;
			if (single?.count === 0) {
				const values = possibleAfters(space, hit);
				if (values?.length) impossible.set(id, nearestAfters(values, hit.after));
				else skipped.add(id);
			} else {
				evidence.push(hit);
				accepted.push(id);
			}
		}
		const prior = infer(space, [], false, preferred, withObservationWeights && !evidence.length);
		const inference = pending ? null : evidence.length ? infer(space, evidence, false, preferred, withObservationWeights) : prior;
		const remaining = /* @__PURE__ */ new Map();
		if (prior?.count && inference) accepted.forEach((id, index) => {
			const count = index === accepted.length - 1 ? inference.count : infer(space, evidence.slice(0, index + 1), true)?.count;
			remaining.set(id, count === void 0 ? null : count / prior.count);
		});
		return {
			prior,
			inference,
			evidence,
			accepted,
			impossible,
			skipped,
			remaining,
			pending
		};
	}
	/** Our move into them, over their surviving bulk. Percent of their max HP. */
	function predictIntoFoe(space, inference, key, rolls, stopAtOne) {
		const { hp } = statTables(space.base);
		const A = space.abilities.length;
		let lo = Infinity;
		let hi = -Infinity;
		let koAll = true;
		let koAny = false;
		let seen = false;
		for (let n = 0; n < space.natures.length; n++) {
			const mult = space.natures[n].mult[key];
			for (let i = 0; i < space.items.length; i++) for (let a = 0; a < A; a++) {
				const proj = inference.bulkProj[key][(n * space.items.length + i) * A + a];
				if (!proj) continue;
				for (let sp = 0; sp < W; sp++) {
					let row;
					for (let h = 0; h < W; h++) {
						if (!proj[h * W + sp]) continue;
						if (row === void 0) {
							row = rolls(space.abilities[a].name, mult, sp, space.items[i].name);
							if (row === void 0) return "pending";
						}
						if (!row || !row.length) continue;
						seen = true;
						const survives = stopAtOne?.(space.items[i].name, space.abilities[a].name);
						const low = Math.min(Math.min(...row), survives ? hp[h] - 1 : Infinity);
						const high = Math.min(Math.max(...row), survives ? hp[h] - 1 : Infinity);
						lo = Math.min(lo, 100 * low / hp[h]);
						hi = Math.max(hi, 100 * high / hp[h]);
						if (low < hp[h]) koAll = false;
						if (high >= hp[h]) koAny = true;
					}
				}
			}
		}
		return seen ? {
			lo,
			hi,
			koAll,
			koAny
		} : null;
	}
	/** Their move into us, over their surviving offense. Our HP points; KO against `ourHP`. */
	function predictIntoMine(space, inference, key, rolls, ourHP, stopAtOne) {
		const I = space.items.length;
		const A = space.abilities.length;
		let lo = Infinity;
		let hi = -Infinity;
		let koAll = true;
		let koAny = false;
		let seen = false;
		for (let n = 0; n < space.natures.length; n++) {
			const mult = space.natures[n].mult[key];
			for (let i = 0; i < I; i++) for (let a = 0; a < A; a++) {
				const proj = inference.offProj[key][(n * I + i) * A + a];
				if (!proj) continue;
				for (let sp = 0; sp < W; sp++) {
					if (!proj[sp]) continue;
					const row = rolls(space.items[i].name, space.abilities[a].name, mult, sp);
					if (row === void 0) return "pending";
					if (!row || !row.length) continue;
					seen = true;
					const survives = stopAtOne?.(space.items[i].name, space.abilities[a].name);
					const low = Math.min(Math.min(...row), survives ? ourHP - 1 : Infinity);
					const high = Math.min(Math.max(...row), survives ? ourHP - 1 : Infinity);
					lo = Math.min(lo, low);
					hi = Math.max(hi, high);
					if (low < ourHP) koAll = false;
					if (high >= ourHP) koAny = true;
				}
			}
		}
		return seen ? {
			lo,
			hi,
			koAll,
			koAny
		} : null;
	}
	//#endregion
	//#region src/pages/calc/infer/observation_information.ts
	/** Readout distributions conditional on the existing model's surviving joint-state weights.
	* This is observation information, not move strength, accuracy, safety or a recommendation. */
	var entropy = (values, total) => {
		let bits = 0;
		for (const value of values) if (value > 0) {
			const p = value / total;
			bits -= p * Math.log2(p);
		}
		return bits;
	};
	/** Marginalise all compatible hidden axes, rather than counting each surviving projection once.
	* A roll has its engine multiplicity; censor at KO or one-HP survival before computing entropy.
	* Full HP is required for a bulk probability: a partial percent identifies several exact current
	* HPs, and the existing model only specifies feasibility over them, not a probability law. */
	function observationInformation(space, inference, hit) {
		if (!inference) return {
			state: "pending",
			reason: "inference_pending"
		};
		if (!inference.count) return {
			state: "unavailable",
			reason: "no_surviving_states"
		};
		const weights = inference.observationWeights;
		if (!weights) return {
			state: "unavailable",
			reason: "joint_weights_not_requested"
		};
		if (hit.kind === "bulk" && hit.before !== 100) return {
			state: "unavailable",
			reason: "partial_hp_probability_unspecified"
		};
		if (!Number.isInteger(hit.before) || hit.before <= 0) return {
			state: "unavailable",
			reason: "invalid_before"
		};
		const W = 33, I = space.items.length, A = space.abilities.length;
		const mass = /* @__PURE__ */ new Map();
		let total = 0, noise = 0;
		for (const [n, nature] of space.natures.entries()) for (const [i, item] of space.items.entries()) for (const [a, ability] of space.abilities.entries()) {
			const index = (n * I + i) * A + a;
			const row = hit.kind === "bulk" ? weights.bulk[hit.key][index] : weights.offense[hit.key][index];
			if (!row) return {
				state: "unavailable",
				reason: "joint_weights_incomplete"
			};
			const stop = hit.stopAtOne?.(item.name, ability.name) ?? false;
			for (let sp = 0; sp < W; sp++) {
				let rolls;
				for (let h = 0; h < (hit.kind === "bulk" ? W : 1); h++) {
					const weight = row[hit.kind === "bulk" ? h * W + sp : sp];
					if (!(weight > 0)) continue;
					if (rolls === void 0) rolls = hit.kind === "bulk" ? hit.rolls(ability.name, nature.mult[hit.key], sp, item.name) : hit.rolls(item.name, ability.name, nature.mult[hit.key], sp);
					if (rolls === void 0) return {
						state: "pending",
						reason: "rolls_pending"
					};
					if (!rolls?.length || rolls.some((d) => !Number.isFinite(d) || d < 0)) return {
						state: "unavailable",
						reason: "rolls_incomplete"
					};
					const hp = hit.kind === "bulk" ? actualStat(space.base.hp, "hp", h, 1) : hit.before;
					const counts = /* @__PURE__ */ new Map();
					for (const damage of rolls) {
						const remaining = Math.max(stop ? 1 : 0, hp - damage);
						const after = hit.kind === "bulk" ? shownPercent(remaining, hp) : remaining;
						counts.set(after, (counts.get(after) ?? 0) + 1);
					}
					total += weight;
					noise += weight * entropy(counts.values(), rolls.length);
					for (const [after, count] of counts) mass.set(after, (mass.get(after) ?? 0) + weight * count / rolls.length);
				}
			}
		}
		if (!(total > 0) || !Number.isFinite(total)) return {
			state: "unavailable",
			reason: "zero_model_weight"
		};
		const readingEntropyBits = entropy(mass.values(), total), expectedNoiseBits = noise / total;
		return {
			state: "ready",
			basis: "surviving_joint_state_weights",
			readout: hit.kind === "bulk" ? "foe_hp_percent" : "our_hp",
			before: hit.before,
			readings: [...mass].sort(([a], [b]) => a - b).map(([after, value]) => ({
				after,
				probability: value / total
			})),
			readingEntropyBits,
			expectedNoiseBits,
			informationGainBits: Math.max(0, readingEntropyBits - expectedNoiseBits)
		};
	}
	//#endregion
	//#region src/pages/calc/infer/shared_runtime.ts
	/** JSON boundary for the same inference model used by the web worker and the local calculator. */
	/** Keys are JSON arrays to avoid collisions between item and ability names. An absent key means the
	* engine has not computed that request; an explicit null means the engine refused it. */
	function evidenceOf(hit) {
		const stopAtOne = (item, ability) => !!hit.stopAtOne?.[JSON.stringify([item, ability])];
		if (hit.kind === "bulk") {
			const rolls = (ability, mult, sp, item) => hit.rolls[JSON.stringify([
				ability,
				mult,
				sp,
				item
			])];
			return {
				kind: "bulk",
				key: hit.key,
				before: hit.before,
				after: hit.after,
				rolls,
				stopAtOne
			};
		}
		const rolls = (item, ability, mult, sp) => hit.rolls[JSON.stringify([
			item,
			ability,
			mult,
			sp
		])];
		return {
			kind: "offense",
			key: hit.key,
			before: hit.before,
			after: hit.after,
			rolls,
			stopAtOne
		};
	}
	function runModel(request) {
		if (request.op === "frame-support") return { reason: frameIssue(request.frame) };
		if (request.op === "survival") return { stopAtOne: survivesHitAtOne(request.attackerAbility, request.attackerAbilityOn, request.defenderItem, request.defenderAbility, request.defenderAbilityOn, request.move) };
		if (request.op === "speed") return projectSpeeds(request.plan, request.speeds);
		if (request.op === "classify") return request.kind === "bulk" ? bulkUse(request.move, request.category) : offenseUse(request.move, request.category);
		if (request.op === "hp") return {
			shown: shownPercent(request.current, request.max),
			range: hpShownAs(request.after, request.max),
			fits: request.kind === "bulk" ? foeRollFits(request.damage, request.max, request.before, request.after, request.stopAtOne) : mineRollFits(request.damage, request.before, request.after, request.stopAtOne)
		};
		if (request.op === "evaluate") {
			const result = evaluateEvidence(request.space, request.hits.map((hit, index) => ({
				id: hit.id ?? String(index),
				evidence: evidenceOf(hit)
			})), void 0, !!request.predictions?.length);
			const predictions = (request.predictions ?? []).map(({ id, hit, ourHP }) => {
				const e = evidenceOf(hit);
				return {
					id,
					prediction: !result.inference ? null : e.kind === "bulk" ? predictIntoFoe(request.space, result.inference, e.key, e.rolls, e.stopAtOne) : predictIntoMine(request.space, result.inference, e.key, e.rolls, ourHP, e.stopAtOne),
					possibleAfters: possibleAfters(request.space, e),
					observation: observationInformation(request.space, result.inference, e)
				};
			});
			return {
				state: result.pending ? "pending" : !result.inference?.count ? "conflict" : "ready",
				count: result.inference?.count ?? null,
				priorCount: result.prior?.count ?? null,
				accepted: result.accepted,
				skipped: [...result.skipped],
				impossible: Object.fromEntries(result.impossible),
				remaining: Object.fromEntries(result.remaining),
				natures: result.inference?.natures ?? null,
				natureShares: result.inference?.natureShares ?? null,
				items: result.inference?.items ?? null,
				abilities: result.inference?.abilities ?? null,
				stats: result.inference?.stats ?? null,
				speedCaps: result.inference?.speedCaps ?? null,
				rulers: result.inference?.rulers ?? null,
				lanes: result.inference?.lanes ?? null,
				mostLikely: result.inference?.mostLikely ?? null,
				...request.predictions ? { predictions } : {}
			};
		}
		const result = infer(request.space, request.hits.map(evidenceOf), !!request.countOnly);
		if (result === null) return { state: "pending" };
		return {
			state: result.count ? "ready" : "conflict",
			count: result.count,
			natures: result.natures,
			natureShares: result.natureShares,
			items: result.items,
			abilities: result.abilities,
			stats: result.stats,
			speedCaps: result.speedCaps,
			rulers: result.rulers,
			lanes: result.lanes,
			mostLikely: result.mostLikely
		};
	}
	//#endregion
	exports.runModel = runModel;
	return exports;
})({});
