#!/bin/env/python3

import sys
import logging
import requests
import argparse

from time import sleep

from elasticsearch.helpers import bulk
from elasticsearch import client

from datetime import datetime


class ELmonocleDB():

    def __init__(self, tenant='default', index='monocle'):
        self.es = client.Elasticsearch('localhost:9200')
        self.index = index
        self.mapping = {
            self.index: {
                "properties": {
                    "id": {"type": "keyword"},
                    "type": {"type": "keyword"},
                    "number": {"type": "keyword"},
                    "title": {"type": "keyword"},
                    "repository_owner": {"type": "keyword"},
                    "repository": {"type": "keyword"},
                    "author": {"type": "keyword"},
                    "committer": {"type": "keyword"},
                    "merged_by": {"type": "keyword"},
                    "created_at": {
                        "type": "date",
                        "format": "date_time_no_millis"
                    },
                    "merged_at": {
                        "type": "date",
                        "format": "date_time_no_millis"
                    },
                    "updated_at": {
                        "type": "date",
                        "format": "date_time_no_millis"
                    },
                    "closed_at": {
                        "type": "date",
                        "format": "date_time_no_millis"
                    },
                    "authored_at": {
                        "type": "date",
                        "format": "date_time_no_millis"
                    },
                    "committed_at": {
                        "type": "date",
                        "format": "date_time_no_millis"
                    },
                    "state": {"type": "keyword"},
                    "duration": {"type": "integer"},
                    "mergeable": {"type": "keyword"},
                    "label": {"type": "keyword"},
                    "assignee": {"type": "keyword"},
                }
            }
        }
        settings = {
            'mappings': self.mapping
        }
        self.ic = client.IndicesClient(self.es)
        self.ic.create(index=self.index, ignore=400, body=settings)

    def update(self, source_it):
        def gen(it):
            for source in it:
                d = {}
                d['_index'] = self.index
                d['_type'] = self.index
                d['_op_type'] = 'update'
                d['_id'] = source['id']
                d['doc'] = source
                d['doc_as_upsert'] = True
                yield d
        bulk(self.es, gen(source_it))
        self.es.indices.refresh(index=self.index)


class GithubGraphQLQuery(object):

    log = logging.getLogger("monocle.GithubGraphQLQuery")

    def __init__(self, token):
        self.url = 'https://api.github.com/graphql'
        self.headers = {'Authorization': 'token %s' % token}
        self.session = requests.session()
        # Will get every 25 requests
        self.get_rate_limit_rate = 25
        self.query_count = 0
        # Set an initial value
        self.quota_remain = 5000
        self.get_rate_limit()

    def get_rate_limit(self):
        try:
            ratelimit = self.getRateLimit()
        except requests.exceptions.ConnectionError:
            sleep(5)
            ratelimit = self.getRateLimit()
        self.quota_remain = ratelimit['remaining']
        self.resetat = datetime.strptime(
            ratelimit['resetAt'], '%Y-%m-%dT%H:%M:%SZ')
        self.log.info("Got rate limit data: remain %s resetat %s" % (
            self.quota_remain, self.resetat))

    def wait_for_call(self):
        if self.quota_remain <= 150:
            until_reset = self.resetat - datetime.utcnow()
            self.log.info(
                "Quota remain: %s/calls delay until "
                "reset: %s/secs waiting ..." % (
                    self.quota_remain, until_reset.seconds))
            sleep(until_reset.seconds + 60)
            self.get_rate_limit()

    def getRateLimit(self):
        qdata = '''{
          rateLimit {
            limit
            cost
            remaining
            resetAt
          }
        }'''
        data = self.query(qdata, skip_get_rate_limit=True)
        return data['data']['rateLimit']

    def query(self, qdata, skip_get_rate_limit=False, ignore_not_found=False):
        if not skip_get_rate_limit:
            if self.query_count % self.get_rate_limit_rate == 0:
                self.get_rate_limit()
            self.wait_for_call()
        data = {'query': qdata}
        r = self.session.post(
            url=self.url, json=data, headers=self.headers,
            timeout=30.3)
        self.query_count += 1
        if not r.status_code != "200":
            raise Exception("No ok response code see: %s" % r.text)
        ret = r.json()
        if 'errors' in ret:
            raise Exception("Errors in response see: %s" % r.text)
        return ret


class PRsFetcher(object):

    log = logging.getLogger("monocle.PRsFetcher")

    def __init__(self, gql, bulk_size=25):
        self.gql = gql
        self.size = bulk_size
        self.qdata = '''{
          search(query: "org:%(org)s is:pr updated:>=%(updated_since)s created:<%(created_before)s" type: ISSUE first: %(size)s %(after)s) {
            issueCount
            pageInfo {
              hasNextPage endCursor
            }
            edges {
              node {
                ... on PullRequest {
                  id
                  updatedAt
                  createdAt
                  mergedAt
                  closedAt
                  title
                  state
                  number
                  mergeable
                  labels (first: 100){
                    edges {
                      node {
                        name
                      }
                    }
                  }
                  assignees (first: 100){
                    edges {
                      node {
                        login
                      }
                    }
                  }
                  comments (first: 100){
                    edges {
                      node {
                        id
                        createdAt
                        author {
                          login
                        }
                      }
                    }
                  }
                  commits (first: 100){
                    edges {
                      node {
                        commit {
                          oid
                          authoredDate
                          committedDate
                          author {
                            user {
                              login
                            }
                          }
                          committer {
                            user {
                              login
                            }
                          }
                        }
                      }
                    }
                  }
                  # reviews (first: 100){
                  #   edges {
                  #     node {
                  #       id
                  #       createdAt
                  #       author {
                  #         login
                  #       }
                  #      comments (first: 100) {
                  #        edges {
                  #          node {
                  #            id
                  #            createdAt
                  #            author {
                  #              login
                  #            }
                  #         }
                  #       }
                  #      }
                  #     }
                  #   }
                  # }
                  timelineItems (first: 100 itemTypes: [CLOSED_EVENT, ASSIGNED_EVENT, CONVERTED_NOTE_TO_ISSUE_EVENT, LABELED_EVENT, PULL_REQUEST_REVIEW]) {
                    edges {
                      node {
                        __typename
                        ... on ClosedEvent {
                          id
                          createdAt
                          actor {
                            login
                          }
                        }
                        ... on AssignedEvent {
                          id
                          createdAt
                          actor {
                            login
                          }
                        }
                        ... on ConvertedNoteToIssueEvent {
                          id
                          createdAt
                          actor {
                            login
                          }
                        }
                        ... on LabeledEvent {
                          id
                          createdAt
                          actor {
                            login
                          }
                        }
                        ... on PullRequestReview {
                          id
                          createdAt
                          author {
                            login
                          }
                        }
                      }
                    }
                  }
                  author {
                    login
                  }
                  mergedBy {
                    login
                  }
                  repository {
                    owner {
                      login
                    }
                    name
                  }
                }
              }
            }
          }
        }'''

    def _getPage(self, kwargs, prs):
        data = self.gql.query(self.qdata % kwargs)
        if not kwargs['total_prs_count']:
            kwargs['total_prs_count'] = data['data']['search']['issueCount']
            self.log.info("Total PRs to fetch: %s" % kwargs['total_prs_count'])
        for pr in data['data']['search']['edges']:
            logging.debug(pr)
            prs.append(pr['node'])
        pageInfo = data['data']['search']['pageInfo']
        if pageInfo['hasNextPage']:
            kwargs['after'] = 'after: "%s"' % pageInfo['endCursor']
            return True
        else:
            return False

    def get(self, org, updated_since):
        prs = []
        kwargs = {
            'org': org,
            'updated_since': updated_since,
            'after': '',
            'created_before': '2019-12-31',
            'total_prs_count': 0,
            'size': self.size
        }

        while True:
            self.log.info('Request %s' % kwargs)
            hnp = self._getPage(kwargs, prs)
            self.log.info("Fetched PRs: %s" % len(prs))
            if not hnp:
                if (len(prs) < kwargs['total_prs_count'] and
                        len(prs) % self.size == 0):
                    kwargs['created_before'] = prs[-1]['createdAt']
                    kwargs['after'] = ''
                    continue
                break
        return prs

    def extract_objects(self, prs):
        def timedelta(start, end):
            format = "%Y-%m-%dT%H:%M:%SZ"
            start = datetime.strptime(start, format)
            end = datetime.strptime(end, format)
            return int((start - end).total_seconds())

        objects = []
        for pr in prs:
            change = {}
            change['type'] = 'ChangeCreatedEvent'
            change['id'] = pr['id']
            change['number'] = pr['number']
            change['repository_owner'] = pr['repository']['owner']['login']
            change['repository'] = pr['repository']['name']
            change['author'] = pr['author']['login']
            change['title'] = pr['title']
            if pr['mergedBy']:
                change['merged_by'] = pr['mergedBy']['login']
            else:
                change['merged_by'] = None
            change['updated_at'] = pr['updatedAt']
            change['created_at'] = pr['createdAt']
            change['merged_at'] = pr['mergedAt']
            change['closed_at'] = pr['closedAt']
            change['state'] = pr['state']
            if pr['state'] == 'CLOSED':
                change['duration'] = timedelta(
                  change['closed_at'], change['created_at'])
            change['mergeable'] = pr['mergeable']
            change['labels'] = tuple(map(
                lambda n: n['node']['name'], pr['labels']['edges']))
            change['assignees'] = tuple(map(
                lambda n: n['node']['login'], pr['assignees']['edges']))
            objects.append(change)
            for comment in pr['comments']['edges']:
                _comment = comment['node']
                objects.append(
                    {
                        'type': 'CommentedEvent',
                        'id': _comment['id'],
                        'created_at': _comment['createdAt'],
                        'author': _comment['author']['login'],
                        'repository_owner': pr['repository']['owner']['login'],
                        'repository': pr['repository']['name'],
                        'number': pr['number'],
                    }
                )
            for commit in pr['commits']['edges']:
                _commit = commit['node']
                obj = {
                    'type': 'CommitCreatedEvent',
                    'id': _commit['commit']['oid'],
                    'authored_at': _commit['commit']['authoredDate'],
                    'committed_at': _commit['commit']['committedDate'],
                    'repository_owner': pr['repository']['owner']['login'],
                    'repository': pr['repository']['name'],
                    'number': pr['number'],
                }
                if _commit['commit']['author']['user']:
                    obj['author'] = _commit[
                      'commit']['author']['user']['login']
                else:
                    obj['author'] = None
                if _commit['commit']['committer']['user']:
                    obj['committer'] = _commit[
                      'commit']['committer']['user']['login']
                else:
                    obj['committer'] = None
                objects.append(obj)
            for timelineitem in pr['timelineItems']['edges']:
                _timelineitem = timelineitem['node']
                obj = {
                    'type': _timelineitem['__typename'],
                    'id': _timelineitem['id'],
                    'created_at': _timelineitem['createdAt'],
                    'author': (
                        _timelineitem.get('actor', {}).get('login') or
                        _timelineitem.get('author', {}).get('login')
                    ),
                    'repository_owner': pr['repository']['owner']['login'],
                    'repository': pr['repository']['name'],
                    'number': pr['number'],
                }
                objects.append(obj)
        return objects


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog='monocle')
    parser.add_argument(
        '--token', help='A Github API token')
    parser.add_argument(
        '--org', help='The Github organization to fetch PR events')
    parser.add_argument(
        '--since', help='Fetch PR updated since')
    parser.add_argument(
        '--loglevel', help='logging level', default='INFO')
    args = parser.parse_args()

    if not all([args.token, args.org, args.since]):
        parser.print_usage()
        sys.exit(1)

    logging.basicConfig(
        level=getattr(logging, args.loglevel.upper()))

    gql = GithubGraphQLQuery(args.token)
    u = PRsFetcher(gql)
    prs = u.get(args.org, args.since)
    objects = u.extract_objects(prs)
    e = ELmonocleDB()
    e.update(objects)
