// Structure-only export of one SmartSHARK project slice for scripts/build_smartshark_linked.py.
// Prints one JSON line. No prose, names or emails leave the database: only ObjectIds, hashes, dates,
// states, numbers, and issue keys parsed out of pull-request titles.
// Expects PROJECT, MAX_SEEDS, SEED_SALT, SEED_MODE, COMMITS_PER_SEED and ISSUE_COMMENTS to be defined before this
// file (the builder prepends them).
// SEED_MODE "pr_commit": developers whose own commits appear in a pull request they opened (t09, t10).
// SEED_MODE "shared_id": person ids that appear on commits, pull requests and mail (t08).

var p = db.project.findOne({name: PROJECT});
var vcs = db.vcs_system.find({project_id: p._id}).map(function(x){return x._id;});
var its = db.issue_system.find({project_id: p._id}).map(function(x){return x._id;});
var prsys = db.pull_request_system.find({project_id: p._id}).map(function(x){return x._id;});
var lists = db.mailing_list.find({project_id: p._id}).map(function(x){return x._id;});
function s(id){ return id ? id.valueOf() : null; }
function iso(d){ return d ? d.toISOString() : null; }
function keyOf(title){ var m = /\b([A-Z][A-Z0-9]+-\d+)\b/.exec(title || ""); return m ? m[1] : null; }

// 1. Seeds: people who authored a git commit that also appears, with the same author id, in a pull
//    request they opened. That pull_request_commit row is the record-level evidence linking the
//    person's GitHub account to their git identity (one ObjectId, so no identities are merged).
var prById = {};
db.pull_request.find({pull_request_system_id: {$in: prsys}},
    {external_id:1, creator_id:1, state:1, created_at:1, merged_at:1, source_repo_url:1, title:1}).forEach(function(x){
  prById[s(x._id)] = {oid: s(x._id), id: x.external_id, creator: s(x.creator_id), state: x.state || "",
                      created: iso(x.created_at), merged: iso(x.merged_at),
                      fork: !!(x.source_repo_url && x.source_repo_url.indexOf("/apache/") < 0),
                      issue_key: keyOf(x.title)};
});
var linked = {};  // creator -> {pr oid -> [sha]}
db.pull_request_commit.find({pull_request_id: {$in: Object.keys(prById).map(function(k){return ObjectId(k);})}},
    {pull_request_id:1, author_id:1, commit_sha:1}).forEach(function(pc){
  var pr = prById[s(pc.pull_request_id)];
  if (!pr || !pc.author_id || s(pc.author_id) !== pr.creator) return;
  var c = db.commit.findOne({vcs_system_id: {$in: vcs}, revision_hash: pc.commit_sha}, {author_id:1});
  if (!c || s(c.author_id) !== pr.creator) return;
  var m = linked[pr.creator] = linked[pr.creator] || {};
  (m[pr.oid] = m[pr.oid] || []).push(pc.commit_sha);
});
var mailCount = {};
db.message.aggregate([{$match: {mailing_list_id: {$in: lists}}}, {$group: {_id: "$from_id", n: {$sum: 1}}}],
                     {allowDiskUse: true}).forEach(function(g){ if (g._id) mailCount[s(g._id)] = g.n; });
var cands = Object.keys(linked).map(function(k){
  return {oid: k, prs: Object.keys(linked[k]).length, mail: mailCount[k] || 0};
});
// prefer people who also write to the mailing list, then those with more linked pull requests;
// SEED_SALT breaks ties deterministically so two tasks from one project can pick different people
cands.sort(function(a, b){
  var am = a.mail > 0 ? 1 : 0, bm = b.mail > 0 ? 1 : 0;
  if (am !== bm) return bm - am;
  if (a.prs !== b.prs) return b.prs - a.prs;
  return (a.oid + SEED_SALT) < (b.oid + SEED_SALT) ? -1 : 1;
});
var seeds = cands.slice(0, MAX_SEEDS).map(function(c){ return c.oid; });
var prs = [], commitShas = {};

if (SEED_MODE === "shared_id") {
  // 1'. Person ids on all three surfaces: git (author or committer), GitHub (pull-request creator, reviewer or
  //     pull-request commit author) and the mailing list. SmartSHARK keys a person on name and email, so these
  //     are people who used one identity everywhere; no ids are merged.
  var onGit = {}, onGh = {}, onMail = {};
  db.commit.find({vcs_system_id: {$in: vcs}}, {author_id:1, committer_id:1}).forEach(function(c){
    if (c.author_id) onGit[s(c.author_id)] = 1; if (c.committer_id) onGit[s(c.committer_id)] = 1; });
  var prOids = Object.keys(prById).map(function(k){ return ObjectId(k); });
  Object.keys(prById).forEach(function(k){ if (prById[k].creator) onGh[prById[k].creator] = 1; });
  db.pull_request_review.find({pull_request_id: {$in: prOids}}, {creator_id:1}).forEach(function(r){ if (r.creator_id) onGh[s(r.creator_id)] = 1; });
  db.pull_request_commit.find({pull_request_id: {$in: prOids}}, {author_id:1}).forEach(function(c){ if (c.author_id) onGh[s(c.author_id)] = 1; });
  Object.keys(mailCount).forEach(function(k){ onMail[k] = 1; });
  seeds = Object.keys(onGit).filter(function(k){ return onGh[k] && onMail[k]; }).sort().slice(0, MAX_SEEDS);
  // 2'. Per seed: up to 2 pull requests they opened (those naming a Jira issue first) and up to 3 of their
  //     commits (those linked to an issue first).
  seeds.forEach(function(oid){
    Object.keys(prById).filter(function(k){ return prById[k].creator === oid; })
      .sort(function(a, b){ return (prById[a].issue_key ? 0 : 1) - (prById[b].issue_key ? 0 : 1) || (a < b ? -1 : 1); })
      .slice(0, 2).forEach(function(proid){
        var pr = prById[proid];
        prs.push({id: String(pr.id), oid: proid, creator: oid, state: pr.state, created: pr.created, merged: pr.merged,
                  fork: pr.fork, issue_key: pr.issue_key, commits: []});
      });
    db.commit.find({vcs_system_id: {$in: vcs}, author_id: ObjectId(oid)}, {revision_hash:1, linked_issue_ids:1})
      .sort({committer_date: 1}).toArray()
      .sort(function(a, b){ return ((a.linked_issue_ids || []).length ? 0 : 1) - ((b.linked_issue_ids || []).length ? 0 : 1); })
      .slice(0, COMMITS_PER_SEED).forEach(function(c){ commitShas[c.revision_hash] = 1; });
  });
  linked = {};
}

// 2. Per seed: up to 2 linked pull requests with up to 3 of their commits.
seeds.forEach(function(oid){
  if (!linked[oid]) return;
  Object.keys(linked[oid]).sort(function(a, b){ return linked[oid][b].length - linked[oid][a].length; })
    .slice(0, 2).forEach(function(proid){
      var pr = prById[proid];
      var shas = linked[oid][proid].slice(0, 3);
      shas.forEach(function(h){ commitShas[h] = 1; });
      prs.push({id: String(pr.id), oid: proid, creator: oid, state: pr.state, created: pr.created, merged: pr.merged,
                fork: pr.fork, issue_key: pr.issue_key, commits: shas});
    });
});

// 2b. Per seed: up to 2 more of their commits that Travis built (CI runs on the main line, not on the
//     pull-request branch commits above).
var built = {};
db.travis_build.find({vcs_system_id: {$in: vcs}}, {commit_id:1}).forEach(function(b){ if (b.commit_id) built[s(b.commit_id)] = 1; });
seeds.forEach(function(oid){
  var n = 0;
  db.commit.find({vcs_system_id: {$in: vcs}, author_id: ObjectId(oid)}, {revision_hash:1}).sort({committer_date: -1}).forEach(function(c){
    if (n < 2 && built[s(c._id)] && !commitShas[c.revision_hash]) { commitShas[c.revision_hash] = 1; n++; }
  });
});

// 3. The commits themselves (real author, committer, dates, parents, linked issues).
var commits = [];
db.commit.find({vcs_system_id: {$in: vcs}, revision_hash: {$in: Object.keys(commitShas)}},
    {revision_hash:1, author_id:1, committer_id:1, author_date:1, committer_date:1, parents:1, linked_issue_ids:1}).forEach(function(c){
  commits.push({hash: c.revision_hash, oid: s(c._id), author: s(c.author_id), committer: s(c.committer_id),
                authored_at: iso(c.author_date), committed_at: iso(c.committer_date), parents: (c.parents || []).slice(0, 2),
                issues: (c.linked_issue_ids || []).map(s)});
});

if (SEED_MODE === "shared_id") {
  var exported = {};
  commits.forEach(function(c){ exported[c.hash] = 1; });
  prs.forEach(function(pr){
    db.pull_request_commit.find({pull_request_id: ObjectId(pr.oid)}, {commit_sha:1}).forEach(function(pc){
      if (exported[pc.commit_sha] && pr.commits.indexOf(pc.commit_sha) < 0) pr.commits.push(pc.commit_sha);
    });
  });
}

// 4. Linked issues: at most one per commit, with reporter, assignee and up to 2 comment authors.
var issueOids = {};
commits.forEach(function(c){ if (c.issues.length) issueOids[c.issues[0]] = 1; });
prs.forEach(function(pr){ if (pr.issue_key) { var i = db.issue.findOne({issue_system_id: {$in: its}, external_id: pr.issue_key}, {_id:1}); if (i) issueOids[s(i._id)] = 1; } });
var issues = [];
db.issue.find({_id: {$in: Object.keys(issueOids).map(function(k){return ObjectId(k);})}},
    {external_id:1, reporter_id:1, assignee_id:1, created_at:1, updated_at:1, status:1, issue_type:1, priority:1, components:1}).forEach(function(i){
  var comments = [];
  db.issue_comment.find({issue_id: i._id, author_id: {$ne: null}}, {author_id:1, created_at:1}).sort({created_at: 1}).limit(ISSUE_COMMENTS).forEach(function(c){
    comments.push({id: s(c._id), author: s(c.author_id), created: iso(c.created_at)});
  });
  issues.push({oid: s(i._id), key: i.external_id, reporter: s(i.reporter_id), assignee: s(i.assignee_id),
               created: iso(i.created_at), updated: iso(i.updated_at), status: i.status || "", type: i.issue_type || "",
               priority: i.priority || "", n_components: (i.components || []).length, comments: comments});
});

// 5. One review per pull request (reviewer may be anyone).
var reviews = [];
prs.forEach(function(pr){
  var r = db.pull_request_review.find({pull_request_id: ObjectId(pr.oid), creator_id: {$ne: null}},
      {creator_id:1, state:1, submitted_at:1}).sort({submitted_at: 1}).limit(1).toArray()[0];
  if (r) reviews.push({id: s(r._id), pr: pr.id, creator: s(r.creator_id), state: r.state || "", at: iso(r.submitted_at)});
});

// 5b. The first pull-request discussion comment by someone other than the creator.
var prComments = [];
prs.forEach(function(pr){
  var c = db.pull_request_comment.find({pull_request_id: ObjectId(pr.oid), author_id: {$nin: [null, ObjectId(pr.creator)]}},
      {author_id:1, created_at:1}).sort({created_at: 1}).limit(1).toArray()[0];
  if (c) prComments.push({id: s(c._id), pr: pr.id, author: s(c.author_id), at: iso(c.created_at)});
});

// 6. Mail from seeds (up to 2 each), keeping thread references between selected messages.
var mail = [];
seeds.forEach(function(oid){
  db.message.find({mailing_list_id: {$in: lists}, from_id: ObjectId(oid)}, {date:1, reference_ids:1, message_id:1})
    .sort({date: 1}).limit(2).forEach(function(m){
      mail.push({oid: s(m._id), from: oid, at: iso(m.date), refs: (m.reference_ids || []).map(s)});
    });
});

// 7. Travis builds of selected commits.
var commitOids = commits.map(function(c){ return ObjectId(c.oid); });
var builds = [];
db.travis_build.find({vcs_system_id: {$in: vcs}, commit_id: {$in: commitOids}},
    {commit_id:1, number:1, state:1, event_type:1, started_at:1, duration:1}).sort({number: 1}).forEach(function(b){
  builds.push({id: s(b._id), commit_oid: s(b.commit_id), number: b.number, state: b.state || "", event: b.event_type || "",
               started: iso(b.started_at), duration: (typeof b.duration === "number") ? b.duration : null});
});

print(JSON.stringify({project: PROJECT, seeds: seeds, prs: prs, commits: commits, issues: issues, reviews: reviews,
                      pr_comments: prComments, mail: mail, builds: builds}));
