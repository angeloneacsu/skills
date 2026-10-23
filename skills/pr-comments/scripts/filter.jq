# Turns one `gh api graphql` response (see query.graphql) into actionable comments.
# Invoke: jq -c --arg me LOGIN --argjson ignore '["x"]' \
#            --slurpfile handled <(pc_handled_json FILE) -f filter.jq response.json
# Emits one object per line: {id, kind, repo, number, title, url, author, createdAt}
def human:
  .author != null
  and .author.__typename != "Bot"
  and (.author.login | endswith("[bot]") | not)
  and .author.login != $me
  and ((.author.login as $l | $ignore | index($l)) == null);

def item($kind; $pr):
  {id, kind: $kind, repo: $pr.repository.nameWithOwner, number: $pr.number,
   title: $pr.title, url, author: .author.login, createdAt};

($handled | map({key: ., value: true}) | from_entries) as $h
| (.data.search.nodes // [])[]
| select(.number != null) as $pr
# my last activity: own comments/reviews + last commit (ISO-8601 UTC sorts lexically)
| ([ ($pr.comments.nodes // [])[], ($pr.reviews.nodes // [])[]
    | select(.author != null and .author.login == $me) | .createdAt ]
   + [ ($pr.commits.nodes // [])[].commit.committedDate ]
   | max // "") as $mine
| (
    ( ($pr.reviewThreads.nodes // [])[]
      | select(.isResolved == false)
      | (.comments.nodes // [])[-1:][] | select(human) | item("thread"; $pr) ),
    ( ($pr.comments.nodes // [])[]
      | select(human and .createdAt > $mine) | item("comment"; $pr) ),
    ( ($pr.reviews.nodes // [])[]
      | select(human and ((.body // "") | test("\\S")) and .createdAt > $mine)
      | item("review"; $pr) )
  )
| select($h[.id] | not)
